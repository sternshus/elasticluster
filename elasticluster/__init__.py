#! /usr/bin/env python
#
#   Copyright (C) 2013 GC3, University of Zurich
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

__author__ = 'Nicolas Baer <nicolas.baer@uzh.ch>'


import logging

logging.basicConfig()
log = logging.getLogger("gc3.elasticluster")
log.DO_NOT_FORK = False

from elasticluster.conf import Configurator

# API
from elasticluster.cluster import Cluster
from elasticluster.repository import AbstractClusterRepository, MultiDiskRepository
from elasticluster.providers import AbstractCloudProvider, AbstractSetupProvider
from elasticluster.providers.ansible_provider import AnsibleSetupProvider
from elasticluster.providers.ec2_boto import BotoCloudProvider
from elasticluster.providers.openstack import OpenStackCloudProvider
from elasticluster.providers.gce import GoogleCloudProvider

def get_configurator(configfiles='~/.elasticluster/config',
                     storage_path=None,
                     include_config_dirs=True):

    conf = Configurator.fromConfig(configfiles,
                                   storage_path,
                                   include_config_dirs=True)
    return conf
