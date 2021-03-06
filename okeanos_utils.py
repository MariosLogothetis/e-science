#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
This script initialize okeanos utils.

@author: Ioannis Stenos, Nick Vrionis
'''

import os
import sys
import nose
import string
import logging

from base64 import b64encode
from os.path import abspath
from datetime import datetime
from kamaki.clients import ClientError
from kamaki.clients.image import ImageClient
from kamaki.clients.astakos import AstakosClient
from kamaki.clients.cyclades import CycladesClient
from kamaki.clients.cyclades import CycladesNetworkClient


# Definitions of return value errors
error_quotas_network = -14
error_get_list_servers = -24
error_get_network_quota = -28
error_create_network = -29
error_get_ip = -30
error_create_server = -31
error_authentication = -99

# Global constants
MAX_WAIT = 300  # Max number of seconds for wait function of Cyclades
REPORT = 25  # Define logging level of REPORT
Bytes_to_GB = 1073741824  # Global to convert bytes to gigabytes
Bytes_to_MB = 1048576  # Global to convert bytes to megabytes
HREF_VALUE_MINUS_PUBLIC_NETWORK_ID = 56  # IpV4 public network id offset



def check_credentials(token, auth_url='https://accounts.okeanos.grnet.gr'
                      '/identity/v2.0'):
    '''Identity,Account/Astakos. Test authentication credentials'''
    logging.log(REPORT, ' Test the credentials')
    try:
        auth = AstakosClient(auth_url, token)
        auth.authenticate()
    except ClientError:
        logging.error('Authentication failed with url %s and token %s' % (
                      auth_url, token))
        sys.exit(error_authentication)
    logging.log(REPORT, ' Authentication verified')
    return auth


def endpoints_and_user_id(auth):
    '''
    Get the endpoints
    Identity, Account --> astakos
    Compute --> cyclades
    Object-store --> pithos
    Image --> plankton
    Network --> network
    '''
    logging.log(REPORT, ' Get the endpoints')
    try:
        endpoints = dict(
            astakos=auth.get_service_endpoints('identity')['publicURL'],
            cyclades=auth.get_service_endpoints('compute')['publicURL'],
            pithos=auth.get_service_endpoints('object-store')['publicURL'],
            plankton=auth.get_service_endpoints('image')['publicURL'],
            network=auth.get_service_endpoints('network')['publicURL']
            )
        user_id = auth.user_info['id']
    except ClientError:
        logging.error('Failed to get endpoints & user_id from identity server')
        raise
    return endpoints, user_id
    
    
def init_cyclades_netclient(endpoint, token):
    '''
    Initialize CycladesNetworkClient
    Cyclades Network client needed for all network functions
    e.g. create network,create floating ip
    '''
    logging.log(REPORT, ' Initialize a cyclades network client')
    try:
        return CycladesNetworkClient(endpoint, token)
    except ClientError:
        logging.error('Failed to initialize cyclades network client')
        raise


def init_plankton(endpoint, token):
    '''
    Plankton/Initialize Imageclient.
    ImageClient has all registered images.
    '''
    logging.log(REPORT, ' Initialize ImageClient')
    try:
        return ImageClient(endpoint, token)
    except ClientError:
        logging.error('Failed to initialize the Image client')
        raise


def init_cyclades(endpoint, token):
    '''
    Compute / Initialize Cyclades client.CycladesClient is used
    to create virtual machines
    '''
    logging.log(REPORT, ' Initialize a cyclades client')
    try:
        return CycladesClient(endpoint, token)
    except ClientError:
        logging.error('Failed to initialize cyclades client')
        raise
    
class Cluster(object):
    '''
    Cluster class represents an entire cluster.Instantiation of cluster gets
    the following arguments: A CycladesClient object,a name-prefix for the
    cluster,the flavors of master and slave machines,the image id of their OS,
    the size of the cluster,a CycladesNetworkClient object and a AstakosClient
    object.
    '''
    def __init__(self, cyclades, prefix, flavor_id_master, flavor_id_slave,
                 image_id, size, net_client, auth_cl):
        self.client = cyclades
        self.nc = net_client
        self.prefix, self.size = prefix, int(size)
        self.flavor_id_master, self.auth = flavor_id_master, auth_cl
        self.flavor_id_slave, self.image_id = flavor_id_slave, image_id

    def get_flo_net_id(self, list_public_networks):
        '''
        Gets an Ipv4 floating network id from the list of public networks Ipv4
        and Ipv6. Takes the href value and removes first 56 characters.
        The number that is left is the public network id
        '''
        float_net_id = 0
        for lst in list_public_networks:
            if(lst['status'] == 'ACTIVE' and
               lst['name'] == 'Public IPv4 Network'):
                    float_net_id = lst['links'][0]['href']
                    break

        try:
            return float_net_id[HREF_VALUE_MINUS_PUBLIC_NETWORK_ID:]
        except TypeError:
            logging.error('Floating Network Id could not be found')
            raise

    def _personality(self, ssh_keys_path='', pub_keys_path=''):
        '''Personality injects ssh keys to the virtual machines we create'''
        personality = []
        if pub_keys_path:
            with open(abspath(pub_keys_path)) as f:
                personality.append(dict(
                    contents=b64encode(f.read()),
                    path='/root/.ssh/authorized_keys',
                    owner='root', group='root', mode=0600))
        if ssh_keys_path or pub_keys_path:
                personality.append(dict(
                    contents=b64encode('StrictHostKeyChecking no'),
                    path='/root/.ssh/config',
                    owner='root', group='root', mode=0600))
        return personality

    def check_network_quota(self):
        '''
        Checks if the user quota is enough to create a new private network
        Subtracts the number of networks used and pending from the max allowed
        number of networks
        '''
        try:
            dict_quotas = self.auth.get_quotas()
        except Exception:
            logging.exception('Error in getting user network quota')
            sys.exit(error_get_network_quota)
        limit_net = dict_quotas['system']['cyclades.network.private']['limit']
        usage_net = dict_quotas['system']['cyclades.network.private']['usage']
        pending_net = \
            dict_quotas['system']['cyclades.network.private']['pending']
        available_networks = limit_net-usage_net-pending_net
        if available_networks >= 1:
            logging.log(REPORT, ' Private Network quota is ok')
            return
        else:
            logging.error('Private Network quota exceeded')
            sys.exit(error_quotas_network)

    def create(self, ssh_k_path='', pub_k_path='', server_log_path=''):
        '''
        Creates a cluster of virtual machines using the Create_server method of
        CycladesClient.
        '''
        logging.log(REPORT, ' Create %s servers prefixed as %s',
                    self.size, self.prefix)
        servers = []
        empty_ip_list = []
        list_of_ports = []
        date_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        # Names the master machine with a timestamp and a prefix name
        # plus number 1
        server_name = '%s%s%s%s%s' % (date_time, '-', self.prefix, '-', 1)
        # Name of the network we will request to create
        net_name = date_time + '-' + self.prefix
        self.check_network_quota()
        # Creates network
        try:
            new_network = self.nc.create_network('MAC_FILTERED', net_name)
        except Exception:
            logging.exception('Error in creating network')
            sys.exit(error_create_network)
        # Gets list of floating ips
        try:
            list_float_ips = self.nc.list_floatingips()
        except Exception:
            logging.exception('Error getting list of floating ips')
            sys.exit(error_get_ip)
        # If there are existing floating ips,we check if there is any free or
        # if all of them are attached to a machine
        if len(list_float_ips) != 0:
            for float_ip in list_float_ips:
                if float_ip['instance_id'] is None:
                    break
                else:
                    count = count+1
                    if count == len(list_float_ips):
                        try:
                            self.nc.create_floatingip(list_float_ips
                                                      [count-1]
                                                      ['floating_network_id'])
                        except ClientError:
                            logging.exception('Cannot create new ip')
                            sys.exit(error_get_ip)
        else:
            # No existing ips,so we create a new one
            # with the floating  network id
            try:
                pub_net_list = self.nc.list_networks()
                float_net_id = self.get_flo_net_id(pub_net_list)
                self.nc.create_floatingip(float_net_id)
            except Exception:
                logging.exception('Error in creating float ip')
                sys.exit(error_get_ip)
        logging.log(REPORT, ' Wait for %s servers to build', self.size)

        # Creation of master server

        try:
            servers.append(self.client.create_server(
                server_name, self.flavor_id_master, self.image_id,
                personality=self._personality(ssh_k_path, pub_k_path)))
        except Exception:
            logging.exception('Error creating master virtual machine'
                              % server_name)
            sys.exit(error_create_server)
        # Creation of slave servers
        for i in range(2, self.size+1):
            try:

                server_name = '%s%s%s%s%s' % (date_time,
                                              '-', self.prefix, '-', i)
                servers.append(self.client.create_server(
                    server_name, self.flavor_id_slave, self.image_id,
                    personality=self._personality(ssh_k_path, pub_k_path),
                    networks=empty_ip_list))

            except Exception:
                logging.exception('Error creating server %s' % server_name)
                sys.exit(error_create_server)
        # We put a wait server for the master here,so we can use the
        # server id later and the slave start their building without
        # waiting for the master to finish building
        try:
            new_status = self.client.wait_server(servers[0]['id'],
                                                 max_wait=MAX_WAIT)
            if not new_status:
                logging.error(' Status for server %s is %s',
                              servers[i]['name'], new_status)
                logging.error(' Program shutting down')
                sys.exit(error_create_server)
            logging.log(REPORT, ' Status for server %s is %s',
                        servers[0]['name'], new_status)
            # Create a subnet for the virtual network between master
            #  and slaves along with the ports needed
            self.nc.create_subnet(new_network['id'], '192.168.0.0/24',
                                  enable_dhcp=True)
            port_details = self.nc.create_port(new_network['id'],
                                               servers[0]['id'])
            self.nc.wait_port(port_details['id'], max_wait=MAX_WAIT)
            # Wait server for the slaves, so we can use their server id
            # in port creation
            for i in range(1, self.size):
                new_status = self.client.wait_server(servers[i]['id'],
                                                     max_wait=MAX_WAIT)
                if not new_status:
                    logging.error(' Status for server %s is %s',
                                  servers[i]['name'], new_status)
                    logging.error(' Program shutting down')
                    sys.exit(error_create_server)
                logging.log(REPORT, ' Status for server %s is %s',
                            servers[i]['name'], new_status)
                port_details = self.nc.create_port(new_network['id'],
                                                   servers[i]['id'])
                list_of_ports.append(port_details)

            for port_details in list_of_ports:
                if port_details['status'] == 'BUILD':
                    status = self.nc.wait_port(port_details['id'],
                                               max_wait=MAX_WAIT)
                    if not status:
                        logging.error(' Status for port %s is %s',
                                      port_details['id'], status)
                        logging.error(' Program shutting down')
                        sys.exit(error_create_server)
        except Exception:
            logging.exception('Error in finalizing cluster creation')
            sys.exit(error_create_server)

        if server_log_path:
            logging.info(' Store passwords in file %s', server_log_path)
            with open(abspath(server_log_path), 'w+') as f:
                from json import dump
                dump(servers, f, indent=2)

        # HOSTNAME_MASTER is always the public ip of master node
        global HOSTNAME_MASTER
        try:
            list_of_servers = self.client.list_servers(detail=True)
        except Exception:
            logging.exception('Could not get list of servers.')
            sys.exit(error_get_list_servers)

        # Find our newly created master server in list of servers
        # Then get its public ip and assign it to HOSTNAME_MASTER
        for server in list_of_servers:
            if servers[0]['name'].rsplit('-', 1)[0] == \
                    server['name'].rsplit('-', 1)[0]:
                for attachment in server['attachments']:
                    if attachment['OS-EXT-IPS:type'] == 'floating':
                        HOSTNAME_MASTER = attachment['ipv4']
        return HOSTNAME_MASTER , servers
