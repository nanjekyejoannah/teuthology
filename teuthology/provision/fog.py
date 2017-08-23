import json
import logging
import os
import requests
import socket
import subprocess
import tempfile
import yaml

from paramiko import AuthenticationException
from paramiko.ssh_exception import NoValidConnectionsError

from ..config import config
from ..contextutil import safe_while
from ..misc import decanonicalize_hostname
from teuthology.exceptions import MaxWhileTries
from teuthology.lock import query
from teuthology.orchestra import remote

log = logging.getLogger(__name__)


class FOG(object):
    def __init__(self, name, os_type, os_version, status=None, user='ubuntu'):
        self.endpoint = config.fog_endpoint
        self.api_token = config.fog_api_token
        self.user_token = config.fog_user_token
        self.name = name
        self.shortname = decanonicalize_hostname(self.name)
        self.os_type = os_type
        self.os_version = os_version
        self.status = status or query.get_status(self.name)
        self.machine_type = self.status['machine_type']
        self.user = user
        self.remote = remote.Remote('%s@%s' % (self.user, self.name))

    def do_request(self, url_suffix, data=None, method='GET', verify=True):
        req_kwargs = dict(
            headers={
                'fog-api-token': self.api_token,
                'fog-user-token': self.user_token,
            },
        )
        if data is not None:
            req_kwargs['data'] = data
        req = requests.Request(
            method,
            self.endpoint + url_suffix,
            **req_kwargs
        )
        prepped = req.prepare()
        resp = requests.Session().send(prepped)
        if not resp.ok and resp.text:
            log.error(resp.text)
        if verify:
            resp.raise_for_status()
        return resp

    def get_host_data(self):
        resp = self.do_request(
            '/host/search/%s' % self.shortname,
        )
        obj = resp.json()
        if obj['count'] == 0:
            raise RuntimeError("Host %s not found!" % self.shortname)
        if obj['count'] > 1:
            raise RuntimeError(
                "More than one host found for %s" % self.shortname)
        return obj['hosts'][0]

    def get_image_data(self):
        name = '%s_%s' % (self.os_type.lower(), self.os_version)
        resp = self.do_request(
            '/image/search/%s' % name,
        )
        obj = resp.json()
        if not obj['count']:
            raise RuntimeError(
                "Could not find an image for %s %s",
                self.os_type,
                self.os_version,
            )
        return obj['images'][0]

    def set_image(self, host_id):
        image_data = self.get_image_data()
        image_id = int(image_data['id'])
        resp = self.do_request(
            '/image/%s/edit' % image_id,
            method='PUT',
            data='{"hosts": %i}' % host_id,
        )
        return resp.ok

    def schedule_deploy_task(self, host_id):
        # First, we need to find the right tasktype ID
        resp = self.do_request(
            '/tasktype/search/deploy',
        )
        tasktypes = [obj for obj in resp.json()['tasktypes']
                     if obj['name'].lower() == 'deploy']
        deploy_id = int(tasktypes[0]['id'])
        # Next, schedule the task
        resp = self.do_request(
            '/host/%i/task' % host_id,
            method='POST',
            data='{"taskTypeID": %i}' % deploy_id,
        )
        print resp.json()
        return resp.ok

    def create(self):
        host_data = self.get_host_data()
        host_id = int(host_data['id'])
        self.set_image(host_id)
        self.schedule_deploy_task(host_id)
        #self.remote.console.power_cycle(timeout=600)
        self.remote.console.power_off()
        self.remote.console.power_on()
        self.remote.console._wait_for_login()
        self._wait_for_ready()
        #return self._create()

    def _create(self):
        pass

    def _wait_for_ready(self):
        with safe_while(sleep=6, tries=20) as proceed:
            while proceed():
                try:
                    self.remote.connect()
                    break
                except (
                    socket.error,
                    NoValidConnectionsError,
                    AuthenticationException,
                    MaxWhileTries,
                ):
                    pass
        #cmd = "while [ ! -e '%s' ]; do sleep 5; done" % self._sentinel_path
        #self.remote.run(args=cmd, timeout=600)
        #log.info("Node is ready: %s", self.node)


    def destroy(self):
        pass

    def build_config(self):
        pass

    def remove_config(self):
        pass

    def __del__(self):
        self.remove_config()
