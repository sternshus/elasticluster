#! /usr/bin/env python
#
# Copyright (C) 2013-2014 GC3, University of Zurich
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
__author__ = 'Nicolas Baer <nicolas.baer@uzh.ch>, Antonio Messina <antonio.messina@s3it.uzh.ch>'

# stdlib imports
from abc import ABCMeta, abstractmethod
from fnmatch import fnmatch
from voluptuous import Invalid
from zipfile import ZipFile
import json
import os
import shutil
import sys
import tempfile

# Elasticluster imports
from elasticluster import get_configurator, log
from elasticluster.exceptions import ClusterNotFound, ConfigurationError, \
    ImageError, SecurityGroupError, NodeNotFound, ClusterError

def ask_confirmation(msg):
    """Ask for confirmation. Returns True or False accordingly"""
    yesno = input(msg + " [yN] ")
    if yesno.lower() not in ['yes', 'y']:
        print("Aborting as per user request.")
        return False
    else:
        return True

class AbstractCommand():
    """
    Defines the general contract every command has to fulfill in
    order to be recognized by the arguments list and executed
    afterwards.
    """
    __metaclass__ = ABCMeta

    def __init__(self, params):
        """
        A reference to the parameters of the command line will be
        passed here to adjust the functionality of the command
        properly.
        """
        self.params = params

    @abstractmethod
    def setup(self, subparsers):
        """
        This method handles the setup of the subcommand. In order to
        do so, every command has to add a parser to the subparsers
        reference given as parameter. The following example is the
        minimum implementation of such a setup procedure: parser =
        subparsers.add_parser("start")
        parser.set_defaults(func=self.execute)
        """
        pass

    @abstractmethod
    def execute(self):
        """
        This method is executed after a command was recognized and may
        vary in its behavior.
        """
        pass

    def __call__(self):
        return self.execute()

    def pre_run(self):
        """
        Overrides this method to execute any pre-run code, especially
        to check any command line options.
        """
        pass


def cluster_summary(cluster):
    try:
        frontend = cluster.get_frontend_node().name
    except NodeNotFound as ex:
        frontend = 'unknown'
        log.error("Unable to get information on the frontend node: "
                  "%s", str(ex))
    msg = """
Cluster name:     %s
Cluster template: %s
Default ssh to node: %s
""" % (cluster.name, cluster.template, frontend)

    for cls in cluster.nodes:
        msg += "- %s nodes: %d\n" % (cls, len(cluster.nodes[cls]))
    msg += """
To login on the frontend node, run the command:

    elasticluster ssh %s

To upload or download files to the cluster, use the command:

    elasticluster sftp %s
""" % (cluster.name, cluster.name)
    return msg


class Start(AbstractCommand):
    """
    Create a new cluster using the given cluster template.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "start", help="Create a cluster using the supplied configuration.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster',
                            help="Type of cluster. It refers to a "
                                 "configuration stanza [cluster/<name>]")
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('-n', '--name', dest='cluster_name',
                            help='Name of the cluster.')
        parser.add_argument('--nodes', metavar='N1:GROUP[,N2:GROUP2,...]',
                            help='Override the values in of the configuration '
                                 'file and starts `N1` nodes of group `GROUP`,'
                                 'N2 of GROUP2 etc...')
        parser.add_argument('--no-setup', action="store_true", default=False,
                            help="Only start the cluster, do not configure it")

    def pre_run(self):
        self.params.extra_conf = {}
        try:
            if self.params.nodes:
                nodes = self.params.nodes.split(',')
                for nspec in nodes:
                    n, group = nspec.split(':')
                    if not n.isdigit():
                        raise ConfigurationError(
                            "Invalid syntax for option `--nodes`: "
                            "`%s` is not an integer." % n)
                    n = int(n)
                    self.params.extra_conf[group + '_nodes'] = n
        except ValueError:
            raise ConfigurationError(
                "Invalid argument for option --nodes: %s" % self.params.nodes)

    def execute(self):
        """
        Starts a new cluster.
        """

        cluster_template = self.params.cluster
        if self.params.cluster_name:
            cluster_name = self.params.cluster_name
        else:
            cluster_name = self.params.cluster

        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)

        # overwrite configuration
        for option, value in self.params.extra_conf.items():
            cconf = configurator.cluster_conf[cluster_template]['cluster']
            if option in cconf:
                cconf[option] = value

        # First, check if the cluster is already created.
        try:
            cluster = configurator.load_cluster(cluster_name)
        except ClusterNotFound as e:
            try:
                cluster = configurator.create_cluster(
                    cluster_template, cluster_name)
            except ConfigurationError as e:
                log.error("Starting cluster %s: %s\n" % (cluster_template, e))
                return

        try:

            for cls in cluster.nodes:
                print("Starting cluster `%s` with %d %s nodes." % (
                    cluster.name, len(cluster.nodes[cls]), cls))
            print("(this may take a while...)")
            conf = configurator.cluster_conf[cluster_template]
            min_nodes = dict(
                (k[:-10], int(v)) for k, v in conf['cluster'].items() if
                k.endswith('_nodes_min'))
            cluster.start(min_nodes=min_nodes)
            if self.params.no_setup:
                print("NOT configuring the cluster as requested.")
            else:
                print("Configuring the cluster.")
                print("(this too may take a while...)")
                ret = cluster.setup()
                if ret:
                    print("Your cluster is ready!")
                else:
                    print("\nWARNING: YOUR CLUSTER IS NOT READY YET!")
            print(cluster_summary(cluster))
        except (KeyError, ImageError, SecurityGroupError, ClusterError) as ex:
            print("Your cluster could not start `%s`" % ex)
            raise


class Stop(AbstractCommand):
    """
    Stop a cluster and terminate all associated virtual machines.
    """

    def setup(self, subparsers):
        """
        @see abstract_command contract
        """
        parser = subparsers.add_parser(
            "stop", help="Stop a cluster and all associated VM instances.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('--force', action="store_true", default=False,
                            help="Remove the cluster even if not all the nodes"
                                 " have been terminated properly.")
        parser.add_argument('--yes', action="store_true", default=False,
                            help="Assume `yes` to all queries and "
                                 "do not prompt.")

    def execute(self):
        """
        Stops the cluster if it's running.
        """
        cluster_name = self.params.cluster
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        try:
            cluster = configurator.load_cluster(cluster_name)
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Stopping cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        if not self.params.yes:
            # Ask for confirmation
            yesno = input(
                "Do you want really want to stop "
                "cluster %s? [yN] " % cluster_name)
            if yesno.lower() not in ['yes', 'y']:
                print("Aborting as per user request.")
                sys.exit(0)
        print("Destroying cluster `%s`" % cluster_name)
        cluster.stop(force=self.params.force)


class ResizeCluster(AbstractCommand):
    """
    Resize the cluster by adding or removing compute nodes.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "resize", help="Resize a cluster by adding or removing "
                           "compute nodes.", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-a', '--add', metavar='N1:GROUP1[,N2:GROUP2]',
                            help="Add N1 nodes of group GROUP1, "
                                 "N2 of group GROUP2 etc...")
        parser.add_argument('-r', '--remove', metavar='N1:GROUP1[,N2:GROUP2]',
                            help="Remove N1 nodes of group GROUP1, "
                                 "N2 of group GROUP2 etc...")
        parser.add_argument('-t', '--template', help='name of the template '
                                                     'of this cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('--no-setup', action="store_true", default=False,
                            help="Only start the cluster, do not configure it")
        parser.add_argument('--yes', action="store_true", default=False,
                            help="Assume `yes` to all queries and "
                                 "do not prompt.")

    def pre_run(self):
        self.params.nodes_to_add = {}
        self.params.nodes_to_remove = {}
        try:
            if self.params.add:
                nodes = self.params.add.split(',')
                for nspec in nodes:
                    n, group = nspec.split(':')
                    if not n.isdigit():
                        raise ConfigurationError(
                            "Invalid syntax for option `--nodes`: "
                            "`%s` is not an integer." % n)
                    self.params.nodes_to_add[group] = int(n)

            if self.params.remove:
                nodes = self.params.remove.split(',')
                for nspec in nodes:
                    n, group = nspec.split(':')
                    self.params.nodes_to_remove[group] = int(n)

        except ValueError as ex:
            raise ConfigurationError(
                "Invalid syntax for argument: %s" % ex)

    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)

        # Get current cluster configuration
        cluster_name = self.params.cluster
        template = self.params.template

        try:
            cluster = configurator.load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Listing nodes from cluster %s: %s\n" %
                      (cluster_name, ex))
            return
        for grp in self.params.nodes_to_add:
            print("Adding %d %s node(s) to the cluster"
                  "" % (self.params.nodes_to_add[grp], grp))

            # Currently we can't save which template was used to setup a
            # cluster, therefore we imply the configuration of the new nodes
            # to match already existent nodes in this group. If no node was
            # added to this group yet, it will abort and ask for the
            # `--template` argument.
            # TODO: find a better solution for this problem, it makes things
            #       complicated for the user
            if (not grp in cluster.nodes or not cluster.nodes[grp]) \
                    and not template:
                print ("Elasticluster can not infer which template to use for "\
                      "the new node(s). Please provide the template with " \
                      "the `-t` or `--template` option")
                return

            if not template:
                sample_node = cluster.nodes[grp][0]
                for i in range(self.params.nodes_to_add[grp]):
                    cluster.add_node(grp,
                                     sample_node.image_id,
                                     sample_node.image_user,
                                     sample_node.flavor,
                                     sample_node.security_group,
                                     image_userdata=sample_node.image_userdata,
                                     **sample_node.extra)
            else:
                conf = configurator.cluster_conf[template]
                conf_kind = conf['nodes'][grp]

                image_user = conf['login']['image_user']
                userdata = conf_kind.get('image_userdata', '')

                extra = conf_kind.copy()
                extra.pop('image_id', None)
                extra.pop('flavor', None)
                extra.pop('security_group', None)
                extra.pop('image_userdata', None)

                for i in range(self.params.nodes_to_add[grp]):
                    cluster.add_node(grp,
                                     conf_kind['image_id'],
                                     image_user,
                                     conf_kind['flavor'],
                                     conf_kind['security_group'],
                                     image_userdata=userdata,
                                     **extra)

        for grp in self.params.nodes_to_remove:
            n_to_rm = self.params.nodes_to_remove[grp]
            print("Removing %d %s node(s) from the cluster."
                  "" % (n_to_rm, grp))
            to_remove = cluster.nodes[grp][-n_to_rm:]
            print("The following nodes will be removed from the cluster.")
            print("    " + str.join("\n    ", [n.name for n in to_remove]))

            if not self.params.yes:
                # Ask for confirmation.
                yesno = input(
                    "Do you really want to remove them? [yN] ")
                if yesno.lower() not in ['yes', 'y']:
                    print("Aborting as per user request.")
                    sys.exit(0)

            for node in to_remove:
                cluster.nodes[grp].remove(node)
                node.stop()

        cluster.start()
        if self.params.no_setup:
            print("NOT configuring the cluster as requested.")
        else:
            print("Reconfiguring the cluster.")
            cluster.setup()
        print(cluster_summary(cluster))


class RemoveNode(AbstractCommand):
    """
    Remove a specific node from the cluster
    """
    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "remove-node", help="Remove a specific node from the cluster",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster',
                            help='Cluster from which the node must be removed')
        parser.add_argument('node', help='Name of node to be removed')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('--no-setup', action="store_true", default=False,
                            help="Do not re-configure the cluster after "
                            "removing the node.")
        parser.add_argument('--yes', action="store_true", default=False,
                            help="Assume `yes` to all queries and "
                                 "do not prompt.")
    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)

        # Get current cluster configuration
        cluster_name = self.params.cluster

        try:
            cluster = configurator.load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Error loading cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        # Find the node to remove.
        try:
            node = cluster.get_node_by_name(self.params.node)
        except NodeNotFound:
            log.error("Node %s not found in cluster %s" % (
                self.params.node, self.params.cluster))
            sys.exit(1)

        # Run
        if not self.params.yes:
            # Ask for confirmation.
            yesno = input(
                "Do you really want to remove node %s? [yN] " % node.name)
            if yesno.lower() not in ['yes', 'y']:
                print("Aborting as per user request.")
                sys.exit(0)

        cluster.remove_node(node, stop=True)
        print("Node %s removed" % node.name)

        if self.params.no_setup:
            print("NOT reconfiguring the cluster as requested.")
        else:
            print("Reconfiguring the cluster.")
            cluster.setup()


class ListClusters(AbstractCommand):
    """
    Print a list of all clusters that have been started.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "list", help="List all started clusters.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")

    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        repository = configurator.create_repository()
        clusters = repository.get_all()

        if not clusters:
            print("No clusters found.")
        else:
            print("""
The following clusters have been started.
Please note that there's no guarantee that they are fully configured:
""")
            for cluster in sorted(clusters):
                print("%s " % cluster.name)
                print("-" * len(cluster.name))
                print("  name:           %s" % cluster.name)
                if cluster.name != cluster.template:
                    print("  template:       %s" % cluster.template)
                for cls in cluster.nodes:
                    print("  - %s nodes: %d" % (cls, len(cluster.nodes[cls])))
                print("")


class ListTemplates(AbstractCommand):
    """
    List the available templates defined in the configuration file.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "list-templates", description=self.__doc__,
            help="Show the templates defined in the configuration file.")

        parser.set_defaults(func=self)
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('clusters', nargs="*",
                            help="List only this cluster. Accepts globbing.")

    def execute(self):

        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        config = configurator.cluster_conf

        print("""%d cluster templates found in configuration file.""" % len(config))
        templates = config.keys()
        for pattern in self.params.clusters:
            templates = [t for t in templates if fnmatch(t, pattern)]

        if self.params.clusters:
            print("""%d cluter templates found matching pattern(s) '%s'""" % (len(templates), str.join(", ", self.params.clusters)))

        for template in templates:
            try:
                cluster = configurator.create_cluster(template, template)
                print("""
name:     %s""" % template)
                for nodekind in cluster.nodes:
                    print("%s nodes: %d" % (
                        nodekind,
                        len(cluster.nodes[nodekind])))
            except ConfigurationError as ex:
                log.error("unable to load cluster `%s`: %s", template, ex)


class ListNodes(AbstractCommand):
    """
    Show some information on all the nodes belonging to a given
    cluster.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "list-nodes", help="Show information about the nodes in the "
                               "cluster", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('--json', action='store_true',
                            help="Produce JSON output")
        parser.add_argument('--pretty-json', action='store_true',
                            help="Produce *indented* JSON output "
                            "(more human readable than --json)")
        parser.add_argument(
            '-u', '--update', action='store_true', default=False,
            help="By default `elasticluster list-nodes` will not contact the "
                 "EC2 provider to get up-to-date information, unless `-u` "
                 "option is given.")

    def execute(self):
        """
        Lists all nodes within the specified cluster with certain
        information like id and ip.
        """
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        cluster_name = self.params.cluster
        try:
            cluster = configurator.load_cluster(cluster_name)
            if self.params.update:
                cluster.update()
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Listing nodes from cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        if self.params.pretty_json:
            print(json.dumps(cluster, default=dict, indent=4))
        elif self.params.json:
            print(json.dumps(cluster, default=dict))
        else:
            print(cluster_summary(cluster))
            for cls in cluster.nodes:
                print("%s nodes:" % cls)
                print("")
                for node in cluster.nodes[cls]:
                    txt = ["    " + i for i in node.pprint().splitlines()]
                    print('  - ' + str.join("\n", txt)[4:])
                    print("")


class SetupCluster(AbstractCommand):
    """
    Setup the given cluster by calling the setup provider defined for
    this cluster.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "setup", help="Configure the cluster.", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")

    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        cluster_name = self.params.cluster
        try:
            cluster = configurator.load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Setting up cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        print("Configuring cluster `%s`..." % cluster_name)
        ret = cluster.setup()
        if ret:
            print("Your cluster is ready!")
        else:
            print("\nWARNING: YOUR CLUSTER IS NOT READY YET!")
        print(cluster_summary(cluster))


class SshFrontend(AbstractCommand):
    """
    Connect to the frontend of the cluster using `ssh`.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "ssh", help="Connect to the frontend of the cluster using the "
                        "`ssh` command", description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('-n', '--node', metavar='HOSTNAME', dest='ssh_to',
                            help="Name of node you want to ssh to. By "
                            "default, the first node of the `ssh_to` option "
                            "group is used.")
        parser.add_argument('ssh_args', metavar='args', nargs='*',
                            help="Execute the following command on the remote "
                            "machine instead of opening an interactive shell.")

    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        cluster_name = self.params.cluster
        try:
            cluster = configurator.load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Setting up cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        if self.params.ssh_to:
            try:
                nodes = dict((n.name,n) for n in cluster.get_all_nodes())
                frontend = nodes[self.params.ssh_to]
            except KeyError:
                raise Invalid(
                    "Hostname %s not found in cluster %s" % (self.params.ssh_to, cluster_name))
        else:
            frontend = cluster.get_frontend_node()
        try:
            # ensure we can connect to the host
            if not frontend.preferred_ip:
                # Ensure we can connect to the node, and save the value of `preferred_ip`

                ssh = frontend.connect(keyfile=cluster.known_hosts_file)
                if ssh:
                    ssh.close()
                cluster.repository.save_or_update(cluster)

        except NodeNotFound as ex:
            log.error("Unable to connect to the frontend node: %s" % str(ex))
            sys.exit(1)
        host = frontend.connection_ip()
        username = frontend.image_user
        knownhostsfile = cluster.known_hosts_file if cluster.known_hosts_file \
                         else '/dev/null'
        ssh_cmdline = ["ssh",
                       "-i", frontend.user_key_private,
                       "-o", "UserKnownHostsFile=%s" % knownhostsfile,
                       "-o", "StrictHostKeyChecking=yes",
                       '%s@%s' % (username, host)]
        ssh_cmdline.extend(self.params.ssh_args)
        log.debug("Running command `%s`" % str.join(' ', ssh_cmdline))
        os.execlp("ssh", *ssh_cmdline)


class SftpFrontend(AbstractCommand):
    """
    Open an SFTP session to the cluster frontend host.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "sftp",
            help="Open an SFTP session to the cluster frontend host.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-n', '--node', metavar='HOSTNAME', dest='ssh_to',
                            help="Name of node you want to ssh to. By "
                            "default, the first node of the `ssh_to` option "
                            "group is used.")
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('sftp_args', metavar='args', nargs='*',
                            help="Arguments to pass to ftp, instead of "
                                 "opening an interactive shell.")

    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        cluster_name = self.params.cluster
        try:
            cluster = configurator.load_cluster(cluster_name)
            cluster.update()
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Setting up cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        if self.params.ssh_to:
            try:
                nodes = dict((n.name,n) for n in cluster.get_all_nodes())
                frontend = nodes[self.params.ssh_to]
            except KeyError:
                raise Invalid(
                    "Hostname %s not found in cluster %s" % (self.params.ssh_to, cluster_name))
        else:
            frontend = cluster.get_frontend_node()
        host = frontend.connection_ip()
        username = frontend.image_user
        knownhostsfile = cluster.known_hosts_file if cluster.known_hosts_file \
                         else '/dev/null'
        sftp_cmdline = ["sftp",
                        "-o", "UserKnownHostsFile=%s" % knownhostsfile,
                        "-o", "StrictHostKeyChecking=yes",
                        "-o", "IdentityFile=%s" % frontend.user_key_private]
        sftp_cmdline.extend(self.params.sftp_args)
        sftp_cmdline.append('%s@%s' % (username, host))
        os.execlp("sftp", *sftp_cmdline)


class GC3PieConfig(AbstractCommand):
    """
    Print a GC3Pie configuration snippet for a specific cluster
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "gc3pie-config", help="Print a GC3Pie configuration snippet.",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('cluster', help='name of the cluster')
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('-a', '--append', metavar='FILE',
                            help='append configuration to file FILE')

    def execute(self):
        """
        Load the cluster and build a GC3Pie configuration snippet.
        """
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        cluster_name = self.params.cluster
        try:
            cluster = configurator.load_cluster(cluster_name)
        except (ClusterNotFound, ConfigurationError) as ex:
            log.error("Listing nodes from cluster %s: %s\n" %
                      (cluster_name, ex))
            return

        from elasticluster.gc3pie_config import create_gc3pie_config_snippet

        if self.params.append:
            path = os.path.expanduser(self.params.append)
            try:
                fd = open(path, 'a')
                fd.write(create_gc3pie_config_snippet(cluster))
                fd.close()
            except IOError as ex:
                log.error("Unable to write configuration to file %s: %s",
                          path, ex)
        else:
            print(create_gc3pie_config_snippet(cluster))

class ExportCluster(AbstractCommand):
    """Save cluster definition in the given file.  A `.zip` extension is
    appended if it's not already there.  By default, the output file is
    named like the cluster.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "export", help="Export a cluster as zip file",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('--overwrite', action='store_true',
                            help='Overwritep ZIP file if it exists.')
        parser.add_argument('--save-keys', action='store_true',
                            help="Also store public and *private* ssh keys. "
                            "WARNING: this will copy sensible data. Use with "
                            "caution!")
        parser.add_argument(
            '-o', '--output-file', metavar='FILE', dest='zipfile',
            help="Output file to be used. By default the cluster is exported "
            "into a <cluster>.zip file where <cluster> is the cluster name.")
        parser.add_argument('cluster', help='Name of the cluster to export.')

    def pre_run(self):
        # find proper path to zip file
        if not self.params.zipfile:
            self.params.zipfile = self.params.cluster + '.zip'

        if not self.params.zipfile.endswith('.zip'):
            self.params.zipfile += '.zip'

    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)

        try:
            cluster = configurator.load_cluster(self.params.cluster)
        except ClusterNotFound:
            log.error("Cluster `%s` not found in storage dir %s."
                      % (self.params.cluster, self.params.storage))
            sys.exit(1)

        if os.path.exists(self.params.zipfile) and not self.params.overwrite:
            log.error("ZIP file `%s` already exists." % self.params.zipfile)
            sys.exit(1)

        with ZipFile(self.params.zipfile, 'w') as zipfile:
            # The root of the zip file will contain:
            # * the storage file
            # * the known_hosts file
            # * ssh public and prived keys, if --save-keys is used
            #
            # it will NOT contain the ansible inventory file, as this
            # is automatically created when needed.
            #
            # Also, if --save-keys is used and there is an host with a
            # different ssh private/public key than the default, they
            # will be saved in:
            #
            #   ./<cluster>/<group>/<nodename>/
            #
            def verbose_add(fname, basedir='', comment=None):
                zipname = basedir + os.path.basename(fname)
                log.info("Adding '%s' as '%s'" % (fname, zipname))
                zipfile.write(fname, zipname)
                if comment:
                    info = zipfile.getinfo(zipname)
                    info.comment = comment

            try:
                verbose_add(cluster.storage_file, comment='cluster-file')
                verbose_add(cluster.known_hosts_file, comment='known_hosts')
                if self.params.save_keys:
                    # that's sensible stuff, let's ask permission.
                    if ask_confirmation("""
WARNING! WARNING! WARNING!
==========================
You are about to add your ssh *private* key to the
ZIP archive. These are sensible data: anyone with
access to the ZIP file will have access to any host
where this private key has been deployed.

Are yousure you still want to copy them?"""):
                        # Also save all the public and private keys we can find.

                        # Cluster keys
                        verbose_add(cluster.user_key_public)
                        verbose_add(cluster.user_key_private)

                        # Node keys, if found
                        for node in cluster.get_all_nodes():
                            if node.user_key_public != cluster.user_key_public:
                                verbose_add(node.user_key_public,
                                            "%s/%s/%s/" % (cluster.name,
                                                           node.kind,
                                                           node.name))
                        for node in cluster.get_all_nodes():
                            if node.user_key_private != cluster.user_key_private:
                                verbose_add(node.user_key_private,
                                            "%s/%s/%s/" % (cluster.name,
                                                           node.kind,
                                                           node.name))
            except OSError as ex:
                # A file is probably missing!
                log.error("Fatal error: cannot add file %s to zip archive: %s."
                          % (ex.filename, ex))
                sys.exit(1)

        print("Cluster '%s' correctly exported into %s" %
              (cluster.name, self.params.zipfile))


class ImportCluster(AbstractCommand):
    """Import a cluster definition from FILE into local storage.
    After running this command, it will be possible to operate
    on the imported cluster as if it had been created locally.

    The FILE to be imported must have been created with
    `elasticluster export`.

    If a cluster already exists with the same name of the one
    being imported, the import operation is aborted and
    `elasticluster` exists with an error.
    """

    def setup(self, subparsers):
        parser = subparsers.add_parser(
            "import", help="Import a cluster from a zip file",
            description=self.__doc__)
        parser.set_defaults(func=self)
        parser.add_argument('-v', '--verbose', action='count', default=0,
                            help="Increase verbosity.")
        parser.add_argument('--rename', metavar='NAME',
                            help="Rename the cluster during import.")
        parser.add_argument("file", help="Path to ZIP file produced by "
                            "`elasticluster export`.")
    def execute(self):
        configurator = get_configurator(self.params.config,
                                        storage_path=self.params.storage,
                                        include_config_dirs=True)
        repo = configurator.create_repository()
        tmpdir = tempfile.mkdtemp()
        log.debug("Using temporary directory %s" % tmpdir)
        tmpconf = get_configurator(self.params.config,
                                   storage_path=tmpdir,
                                   include_config_dirs=True)
        tmprepo =tmpconf.create_repository()

        rc=0
        # Read the zip file.
        try:
            with ZipFile(self.params.file, 'r') as zipfile:
                # Find main cluster file
                # create cluster object from it
                log.debug("ZIP file %s opened" % self.params.file)
                cluster = None
                zipfile.extractall(tmpdir)
                newclusters = tmprepo.get_all()
                cluster = newclusters[0]
                cur_clusternames = [c.name for c in repo.get_all()]
                oldname = cluster.name
                newname = self.params.rename
                if self.params.rename:
                    cluster.name = self.params.rename
                    for node in cluster.get_all_nodes():
                        node.cluster_name = cluster.name
                if cluster.name in cur_clusternames:
                    raise Exception(
                        "A cluster with name %s already exists. Use "
                        "option --rename to rename the cluster to be "
                        "imported." % cluster.name)

                        # Save the cluster in the new position
                cluster.repository = repo
                repo.save_or_update(cluster)
                dest = cluster.repository.storage_path

                # Copy the known hosts
                srcfile = os.path.join(tmpdir, oldname+'.known_hosts')
                destfile = os.path.join(dest, cluster.name+'.known_hosts')
                shutil.copy(srcfile, destfile)

                # Copy the ssh keys, if present
                for attr in ('user_key_public', 'user_key_private'):
                    keyfile = getattr(cluster, attr)
                    keybase = os.path.basename(keyfile)
                    srcfile = os.path.join(tmpdir, keybase)
                    if os.path.isfile(srcfile):
                        log.info("Importing key file %s" % keybase)
                        destfile = os.path.join(dest, keybase)
                        shutil.copy(srcfile, destfile)
                        setattr(cluster, attr, destfile)

                    for node in cluster.get_all_nodes():
                        nodekeyfile = getattr(node, attr)
                        # Check if it's different from the main key
                        if nodekeyfile != keyfile \
                           and os.path.isfile(nodekeyfile):
                            destdir = os.path.join(dest,
                                                   cluster.name,
                                                   node.kind,
                                                   node.name)
                            nodekeybase = os.path.basename(nodekeyfile)
                            log.info("Importing key file %s for node %s" %
                                     (nodekeybase, node.name))
                            if not os.path.isdir(destdir):
                                os.makedirs(destdir)
                            # Path to key in zip file
                            srcfile = os.path.join(tmpdir,
                                                   oldname,
                                                   node.kind,
                                                   node.name,
                                                   nodekeybase)
                            destfile = os.path.join(destdir, nodekeybase)
                            shutil.copy(srcfile, destfile)
                        # Always save the correct destfile
                        setattr(node, attr, destfile)

                repo.save_or_update(cluster)
                if not cluster:
                    log.error("ZIP file %s does not contain a valid cluster."
                              % self.params.file)
                    rc = 2

                # Check if a cluster already exists.
                # if not, unzip the needed files, and update ssh key path if needed.
        except Exception as ex:
            log.error("Unable to import from zipfile %s: %s"
                      % (self.params.file, ex))
            rc=1
        finally:
            if os.path.isdir(tmpdir):
                shutil.rmtree(tmpdir)
            log.info("Cleaning up directory %s" % tmpdir)

        if rc == 0:
            print("Successfully imported cluster from ZIP %s to %s"
                  % (self.params.file, repo.storage_path))
        sys.exit(rc)
