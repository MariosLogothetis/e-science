#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
This script creates a virtual cluster on ~okeanos and installs Hadoop
using Ansible.

@author: Ioannis Stenos, Nick Vrionis
'''

import sys
import os
import nose
import logging
import subprocess
import re
import string
import paramiko
from sys import argv
from reroute_ssh import *

# Definitions of return value errors
error_ansible_playbook = -34

# Global constants
ADD_TO_GET_PORT = 9998  # Value to add in order to get slave port numbers
REPORT = 25  # Define logging level of REPORT
ANSIBLE_DIR = './ansible/'
ANSIBLE_HOST_PATH = ANSIBLE_DIR + 'ansible_hosts'
ANSIBLE_PLAYBOOK_PATH = ANSIBLE_DIR + 'site.yml'

def install_yarn(hosts_list , master_ip, cluster_name):
    '''
    Calls ansible playbook for the installation of yarn and all
    required dependencies. Also  formats and starts yarn.
    '''
    global HOSTNAME_MASTER , list_of_hosts
    list_of_hosts = hosts_list
    HOSTNAME_MASTER = master_ip
    # Create ansible_hosts file
    try:
        file_name = create_ansible_hosts(cluster_name)
        # Run Ansible playbook
        run_ansible(file_name)
        logging.log(REPORT,' Cluster is active. You can access it through ' + HOSTNAME_MASTER + ':8088/cluster')		
    except Exception, e:
        logging.error(' Program is exiting')
        sys.exit(error_ansible_playbook)
            
def create_ansible_hosts(cluster_name):
    '''
    Function that creates the ansible_hosts file and
    returns the name of the file.
    '''
    ansible_hosts_prefix = cluster_name.replace(" ", "")
    ansible_hosts_prefix = ansible_hosts_prefix.replace(":", "")

    # Removes spaces and ':' from cluster name and appends it to ansible_hosts
    # The ansible_hosts file will now have a timestamped name to seperate it
    # from ansible_hosts files of different clusters.
    filename = ANSIBLE_HOST_PATH + ansible_hosts_prefix

    # Create ansible_hosts file and write all information that is
    # required from Ansible playbook.
    with open(filename, 'w+') as target:
        target.write('[master]' + '\n')
        target.write(list_of_hosts[0]['fqdn'])
        target.write(' private_ip='+list_of_hosts[0]['private_ip'])
        target.write(' ansible_ssh_host=' + HOSTNAME_MASTER + '\n' + '\n')
        target.write('[slaves]'+'\n')

        for host in list_of_hosts[1:]:
            target.write(host['fqdn'])
            target.write(' private_ip='+host['private_ip'])
            target.write(' ansible_ssh_port='+str(host['port']))
            target.write(' ansible_ssh_host='+list_of_hosts[0]['fqdn'] + '\n')
    return filename
                
def run_ansible(filename):
    '''
    Calls the ansible playbook that installs and configures
    hadoop and everything needed for hadoop to be functional.
    Filename as argument is the name of ansible_hosts file.
    '''
    logging.log(REPORT, ' Ansible starts Hadoop installation on master and '
                        'slave nodes')
    # First time call of Ansible playbook install.yml executes tasks
    # required for hadoop installation on every virtual machine. Runs with
    # -f flag which is the fork argument of Ansible. Fork number used is size
    # of cluster.
    exit_status = os.system('export ANSIBLE_HOST_KEY_CHECKING=False;'
                            'ansible-playbook -i ' + filename + ' ' +
                            ANSIBLE_PLAYBOOK_PATH + ' -e "choose_role=yarn format=True start_yarn=True"' )
    if exit_status != 0:
        logging.error(' Ansible failed')
        raise RuntimeError

def main(opts):
    '''
    The main function calls reroute_ssh_prep with the arguments given from
    command line.
    '''
    reroute_ssh_prep(opts.hosts_list,opts.master_ip,opts.cluster_name)


if __name__ == '__main__':

    #  Add some interaction candy

    kw = {}
    kw['usage'] = '%prog [options]'
    kw['description'] = '%prog deploys a compute cluster on Synnefo w. kamaki'



    parser = OptionParser(**kw)
    parser.disable_interspersed_args()
    parser.add_option('--server',
                      action='store', type='string', dest='server',
                      metavar="SERVER",
                      help='it is  a list with informatinos about the cluster(names and fqdn of the nodes)')
    parser.add_option('--public_ip',
                      action='store', type='string', dest='public_ip',
                      metavar="PUBLIC_IP",
                      help='it is the ipv4 of the master node ')
    parser.add_option('--cluster_name',
                      action='store', type='string', dest='cluster_name',
                      metavar='CLUSTER_NAME',
                      help='the name of the cluster')


    main(opts)




