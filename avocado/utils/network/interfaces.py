# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
#
# Copyright: 2019-2020 IBM
# Copyright: 2019-2020 Red Hat Inc.
# Authors : Beraldo Leal <bleal@redhat.com>
#         : Praveen K Pandey <praveen@linux.vnet.ibm.com>
#         : Vaishnavi Bhat <vaishnavi@linux.vnet.ibm.com>

import json
import logging
import os
import shutil
import warnings
from ipaddress import ip_interface

from ..distro import detect as distro_detect
from ..process import CmdError
from ..wait import wait_for
from .common import run_command
from .exceptions import NWException

log = logging.getLogger('avocado.test')


class NetworkInterface:
    """
    This class represents a network card interface (NIC).

    An "NetworkInterface" is attached to some host. This could be an instance
    of LocalHost or RemoteHost.  If a RemoteHost then all commands will be
    executed on a remote_session (host.remote_session). Otherwise will be
    executed locally.

    Here you will find a few methods to perform basic operations on a NIC.
    """

    def __init__(self, if_name, host, if_type='Ethernet'):
        self.name = if_name
        self.if_type = if_type
        self.host = host

    def _get_interface_details(self, version=4):
        cmd = "ip -{} -j address show {}".format(version, self.name)
        output = run_command(cmd, self.host)
        try:
            result = json.loads(output)
            for item in result:
                if item.get('ifname') == self.name:
                    return item
            raise NWException("Interface not found")
        except (NWException, json.JSONDecodeError):
            msg = "Unable to get IP address on interface {}".format(self.name)
            log.error(msg)
            raise NWException(msg)

    def _move_file_to_backup(self, filename, ignore_missing=True):
        destination = "{}.backup".format(filename)
        if os.path.exists(filename):
            shutil.move(filename, destination)
        else:
            if not ignore_missing:
                raise NWException("%s interface not available" % self.name)

    def _write_to_file(self, filename, values):
        self._move_file_to_backup(filename)

        with open(filename, 'w+') as fp:
            for key, value in values.items():
                fp.write("{}={}\n".format(key, value))

    def set_hwaddr(self, hwaddr):
        """Sets a Hardware Address (MAC Address) to the interface.

        This method will try to set a new hwaddr to this interface, if
        fails it will raise a NWException.

        You must have sudo permissions to run this method on a host.

        :param hwaddr: Hardware Address (Mac Address)
        """
        cmd = "ip link set dev {} address {}".format(self.name, hwaddr)
        try:
            run_command(cmd, self.self.host, sudo=True)
        except Exception as ex:
            raise NWException("Adding hw address fails: %s" % ex)

    def add_ipaddr(self, ipaddr, netmask):
        """Add an IP Address (with netmask) to the interface.

        This method will try to add a new ipaddr/netmask this interface, if
        fails it will raise a NWException.

        You must have sudo permissions to run this method on a host.

        :param ipaddr: IP Address
        :param netmask: Network mask
        """

        ip = ip_interface("{}/{}".format(ipaddr, netmask))
        cmd = 'ip addr add {} dev {}'.format(ip.compressed,
                                             self.name)
        try:
            run_command(cmd, self.host, sudo=True)
        except Exception as ex:
            raise NWException("Failed to add address {}".format(ex))

    def bring_down(self):
        """Shutdown the interface.

        This will shutdown the interface link. Be careful, you might lost
        connection to the host.

        You must have sudo permissions to run this method on a host.
        """

        cmd = "ip link set {} down".format(self.name)
        try:
            run_command(cmd, self.host, sudo=True)
        except Exception as ex:
            raise NWException("Failed to bring down: %s" % ex)

    def bring_up(self):
        """"Wake-up the interface.

        This will wake-up the interface link.

        You must have sudo permissions to run this method on a host.
        """
        cmd = "ip link set {} up".format(self.name)
        try:
            run_command(cmd, self.host, sudo=True)
        except Exception as ex:
            raise NWException("Failed to bring up: %s" % ex)

    def is_admin_link_up(self):
        """Check the admin link state is up or not.

        :return: True or False, True if network interface state is 'UP'
                 otherwise will return False.
        """
        try:
            if self._get_interface_details().get('flags')[2] == 'UP':
                return True
        except (NWException, IndexError):
            raise NWException("Could not get Administrative link state.")
        return False

    def is_operational_link_up(self):
        """Check Operational link state is up or not.

        :return: True or False. True if operational link state is LOWER_UP,
                 otherwise will return False.
        """
        try:
            if self._get_interface_details().get('flags')[3] == 'LOWER_UP':
                return True
        except (NWException, IndexError):
            raise NWException("Could not get operational link state.")
        return False

    def is_link_up(self):
        """Check if the interface is up or not.

        :return: True or False. True if admin link state and operational
                 link state is up otherwise will return False.
        """
        return self.is_admin_link_up() and self.is_operational_link_up()

    def get_default_interface(self):
        """Get the default interface.

        :return: Default interface name.
        """
        output = run_command("ip route", self.host)
        try:
            for line in output.splitlines():
                if "default" in line:
                    return line.split()[4]
        except Exception as ex:
            raise NWException("could not get default interface: {}".format(ex))

    def get_ipaddrs(self, version=4):
        """Get the IP addresses from a network interface.

        Interfaces can hold multiple IP addresses. This method will return a
        list with all addresses on this interface.

        :param version: Address Family Version (4 or 6). This must be a integer
                        and default is 4.
        :return: IP address as string.
        """
        if version not in [4, 6]:
            raise NWException("Version {} not supported".format(version))

        try:
            details = self._get_interface_details(version)
            addr_info = details.get('addr_info')
            if addr_info:
                return [x.get('local') for x in addr_info]
        except (NWException, IndexError):
            msg = "Could not get ip addresses for {}".format(self.name)
            log.debug(msg)
            return []

    def get_hwaddr(self):
        """Get the Hardware Address (MAC) of this interface.

        This method will try to get the address and if fails it will raise a
        NWException.
        """
        cmd = "cat /sys/class/net/{}/address".format(self.name)
        try:
            return run_command(cmd, self.host)
        except Exception as ex:
            raise NWException("Failed to get hw address: {}".format(ex))

    def get_link_state(self):
        """Method used to get the current link state of this interface.

        This method will return 'up', 'down' or 'unknown', based on the
        network interface state. Or it will raise a NWException if is
        unable to get the interface state.
        """
        warnings.warn("deprecated, use existing methods: is_operational_link_up,\
                       is_admin_link_up", DeprecationWarning)
        cmd = "cat /sys/class/net/{}/operstate".format(self.name)
        try:
            return run_command(cmd, self.host)
        except CmdError as e:
            msg = ('Failed to get link state. Maybe the interface is '
                   'missing. {}'.format(e))
            raise NWException(msg)

    def get_mtu(self):
        """Return the current MTU value of this interface.
        This method will try to get the current MTU value, if fails will
        raise a NWException.
        """
        try:
            return self._get_interface_details().get('mtu')
        except (NWException, IndexError):
            raise NWException("Could not get MUT value.")

    def ping_check(self, peer_ip, count=2, options=None):
        """This method will try to ping a peer address (IPv4 or IPv6).

        You should provide a IPv4 or IPV6 that would like to ping. This
        method will try to ping the peer and if fails it will raise a
        NWException.

        :param peer_ip: Peer IP address (IPv4 or IPv6)
        :param count: How many packets to send. Default is 2
        :param options: ping command options. Default is None
        """
        cmd = "ping -I {} {} -c {}".format(self.name, peer_ip, count)
        if options is not None:
            cmd = "{} {}".format(cmd, options)
        try:
            run_command(cmd, self.host)
        except Exception as ex:
            raise NWException("Failed to ping: {}".format(ex))

    def save(self, ipaddr, netmask):
        """Save current interface IP Address to the system configuration file.

        If the ipaddr is valid (currently being used by the interface)
        this will try to save the current settings into /etc/. This
        check is necessary to avoid inconsistency. Before save, you
        should add_ipaddr, first.

        Currently, only RHEL, Fedora and SuSE are supported. And this
        will create a backup file of your current configuration if
        found.

        :param ipaddr : IP Address which need to configure for interface
        :param netmask: Network mask which is associated to the provided IP
        """
        if ipaddr not in self.get_ipaddrs():
            msg = ('ipaddr not configured on interface. To avoid '
                   'inconsistency, please add the ipaddr first.')
            raise NWException(msg)

        current_distro = distro_detect()

        filename = "ifcfg-{}".format(self.name)
        if current_distro.name in ['rhel', 'fedora']:
            path = "/etc/sysconfig/network-scripts"
        elif current_distro.name == 'SuSE':
            path = "/etc/sysconfig/network"
        else:
            msg = 'Distro not supported by API. Could not save ipaddr.'
            raise NWException(msg)

        self._write_to_file("{}/{}".format(path, filename),
                            {'TYPE': self.if_type,
                             'BOOTPROTO': 'none',
                             'NAME': self.name,
                             'DEVICE': self.name,
                             'ONBOOT': 'yes',
                             'IPADDR': ipaddr,
                             'NETMASK': netmask,
                             'IPV6INIT': 'yes',
                             'IPV6_AUTOCONF': 'yes',
                             'IPV6_DEFROUTE': 'yes'})

    def set_mtu(self, mtu, timeout=30):
        """Sets a new MTU value to this interface.

        This method will try to set a new MTU value to this interface,
        if fails it will raise a NWException. Also it will wait until
        the Interface is up before returning or until timeout be
        reached.

        You must have sudo permissions to run this method on a host.

        :param mtu:  mtu size that need to be set. This must be an int.
        :param timeout: how many seconds to wait until the interface is
                        up again. Default is 30.
        """
        cmd = "ip link set %s mtu %s" % (self.name, mtu)
        run_command(cmd, self.host, sudo=True)
        wait_for(self.is_link_up, timeout=timeout)
        if int(mtu) != self.get_mtu():
            raise NWException("Failed to set MTU.")

    def remove_ipaddr(self, ipaddr, netmask):
        """Removes an IP address from this interface.

        This method will try to remove the address from this interface
        and if fails it will raise a NWException. Be careful, you can
        lost connection.

        You must have sudo permissions to run this method on a host.
        """
        ip = ip_interface("{}/{}".format(ipaddr, netmask))
        cmd = 'ip addr del {} dev {}'.format(ip.compressed,
                                             self.name)
        try:
            run_command(cmd, self.host, sudo=True)
        except Exception as ex:
            msg = 'Failed to remove ipaddr. {}'.format(ex)
            raise NWException(msg)

    def restore_from_backup(self):
        """Revert interface file from backup.

        This method checks if a backup version  is available for given interface
        then it copies backup file to interface file in /sysfs path
        """

        current_distro = distro_detect()
        filename = "ifcfg-{}".format(self.name)
        if current_distro.name in ['rhel', 'fedora']:
            path = "/etc/sysconfig/network-scripts"
        elif current_distro.name == 'SuSE':
            path = "/etc/sysconfig/network"
        else:
            msg = 'Distro not supported by API. Could not restore the backup file.'
            raise NWException(msg)
        filename = "{}/{}".format(path, filename)
        backup_file = "{}.backup".format(filename)
        if os.path.exists(backup_file):
            shutil.move(backup_file, filename)
        else:
            raise NWException(
                "Backup file not available, could not restore file.")
