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
# Copyright: Red Hat Inc. 2020
# Authors: Beraldo Leal <bleal@redhat.com>

"""
Jobs subcommand
"""
import json
import os
from datetime import datetime
from glob import glob

from avocado.core import exit_codes, output
from avocado.core.data_dir import get_job_results_dir, get_logs_dir
from avocado.core.future.settings import settings
from avocado.core.output import LOG_UI
from avocado.core.plugin_interfaces import CLICmd
from avocado.core.spawners.exceptions import SpawnerException
from avocado.core.spawners.podman import PodmanSpawner
from avocado.core.spawners.process import ProcessSpawner
from avocado.utils import astring


class Jobs(CLICmd):
    """
    Implements the avocado 'jobs' subcommand
    """
    name = 'jobs'
    description = 'Manage Avocado jobs'

    def _get_data_from_file(self, filename):
        if not filename or not os.path.isfile(filename):
            raise FileNotFoundError('File not found {}'.format(filename))

        with open(filename, 'r') as fp:
            return json.load(fp)

    def _print_job_details(self, details):
        for key, value in details.items():
            LOG_UI.info("%-12s: %s", key, value)

    def _print_job_tests(self, tests):
        test_matrix = []
        date_fmt = "%Y/%m/%d %H:%M:%S"
        for test in tests:
            status = test.get('status')
            decorator = output.TEST_STATUS_DECORATOR_MAPPING.get(status)
            end = datetime.fromtimestamp(test.get('end'))
            test_matrix.append((test.get('id'),
                                end.strftime(date_fmt),
                                "%5f" % float(test.get('time')),
                                decorator(status, '')))
        header = (output.TERM_SUPPORT.header_str('Test ID'),
                  output.TERM_SUPPORT.header_str('End Time'),
                  output.TERM_SUPPORT.header_str('Run Time'),
                  output.TERM_SUPPORT.header_str('Status'))
        for line in astring.iter_tabular_output(test_matrix,
                                                header=header,
                                                strip=True):
            LOG_UI.debug(line)

    def _save_stream_to_file(self, stream, filename):
        """Save stream to a file.

        Directory must exists before calling this function.
        """
        dirname = os.path.dirname(filename)
        os.makedirs(dirname, exist_ok=True)

        with open(filename, 'ab') as output_file:
            output_file.write(stream)

    def configure(self, parser):
        """
        Add the subparser for the assets action.

        :param parser: The Avocado command line application parser
        :type parser: :class:`avocado.core.parser.ArgumentParser`
        """
        parser = super(Jobs, self).configure(parser)

        subcommands = parser.add_subparsers(dest='jobs_subcommand',
                                            metavar='sub-command')
        subcommands.required = True

        help_msg = 'List all known jobs by Avocado'
        subcommands.add_parser('list', help=help_msg)

        help_msg = ('Show details about a specific job. When passing a Job '
                    'ID, you can use any Job Reference (job_id, "latest", '
                    'or job results path).')
        show_parser = subcommands.add_parser('show', help=help_msg)
        settings.register_option(section='jobs.show',
                                 key='job_id',
                                 help_msg='JOB id',
                                 metavar='JOBID',
                                 default='latest',
                                 nargs='?',
                                 positional_arg=True,
                                 parser=show_parser)
        help_msg = ('Download output files generated by tests on '
                    'AVOCADO_TEST_OUTPUT_DIR')
        output_files_parser = subcommands.add_parser('get-output-files',
                                                     help=help_msg)
        settings.register_option(section='jobs.get.output_files',
                                 key='job_id',
                                 help_msg='JOB id',
                                 metavar='JOBID',
                                 default=None,
                                 positional_arg=True,
                                 parser=output_files_parser)

        settings.register_option(section='jobs.get.output_files',
                                 key='destination',
                                 help_msg='Destination path',
                                 metavar='DESTINATION',
                                 default=None,
                                 positional_arg=True,
                                 parser=output_files_parser)

    def handle_list_command(self, jobs_results):
        """Called when 'avocado jobs list' command is executed."""

        for filename in jobs_results.values():
            with open(filename, 'r') as fp:
                job = json.load(fp)
                try:
                    started_ts = job['tests'][0]['start']
                    started = datetime.fromtimestamp(started_ts)
                except IndexError:
                    continue
                LOG_UI.info("%-40s %-26s %3s (%s/%s/%s/%s)",
                            job['job_id'],
                            str(started),
                            job['total'],
                            job['pass'],
                            job['skip'],
                            job['errors'],
                            job['failures'])

        return exit_codes.AVOCADO_ALL_OK

    def _download_tests(self, tests, destination, job_id, spawner):
        for test in tests:
            test_id = test.get('id')
            LOG_UI.info("Downloading files for test %s", test_id)
            try:
                files_buffers = spawner().stream_output(job_id, test_id)
                for filename, stream in files_buffers:
                    dest = os.path.join(destination,
                                        test_id.replace('/', '_'),
                                        filename)
                    self._save_stream_to_file(stream, dest)
            except SpawnerException as ex:
                LOG_UI.error("Error: Failed to download: %s. Exiting...", ex)
                return exit_codes.AVOCADO_GENERIC_CRASH
        return exit_codes.AVOCADO_ALL_OK

    def handle_output_files_command(self, config):
        """Called when 'avocado jobs get-output-files' command is executed."""

        job_id = config.get('jobs.get.output_files.job_id')
        destination = config.get('jobs.get.output_files.destination')

        results_dir = get_job_results_dir(job_id)
        results_file = os.path.join(results_dir, 'results.json')
        config_file = os.path.join(results_dir, 'jobdata/args.json')

        try:
            config_data = self._get_data_from_file(config_file)
            results_data = self._get_data_from_file(results_file)
        except FileNotFoundError as ex:
            LOG_UI.error("Could not get job information: %s", ex)
            return exit_codes.AVOCADO_GENERIC_CRASH

        spawners = {'process': ProcessSpawner,
                    'podman': PodmanSpawner}

        spawner_name = config_data.get('nrun.spawner')
        spawner = spawners.get(spawner_name)

        if spawner is None:
            msg = ("Could not find the spawner for job %s. This command is "
                   "experimental and only supported when job executed with "
                   "the Spawner architecture.")
            LOG_UI.error(msg, job_id)
            return exit_codes.AVOCADO_GENERIC_CRASH

        return self._download_tests(results_data.get('tests'),
                                    destination,
                                    job_id,
                                    spawner)

    def handle_show_command(self, config):
        """Called when 'avocado jobs show' command is executed."""

        job_id = config.get('jobs.show.job_id')
        results_dir = get_job_results_dir(job_id)
        if results_dir is None:
            LOG_UI.error("Error: Job %s not found", job_id)
            return exit_codes.AVOCADO_GENERIC_CRASH

        results_file = os.path.join(results_dir, 'results.json')
        config_file = os.path.join(results_dir, 'jobdata/args.json')
        try:
            results_data = self._get_data_from_file(results_file)
        except FileNotFoundError as ex:
            # Results data are important and should exit if not found
            LOG_UI.error(ex)
            return exit_codes.AVOCADO_GENERIC_CRASH

        try:
            config_data = self._get_data_from_file(config_file)
        except FileNotFoundError:
            pass

        data = {'JOB ID': job_id,
                'JOB LOG': results_data.get('debuglog'),
                'SPAWNER': config_data.get('nrun.spawner', 'unknown')}

        # We could improve this soon with more data and colors
        self._print_job_details(data)
        LOG_UI.info("")
        self._print_job_tests(results_data.get('tests'))
        results = ('PASS %d | ERROR %d | FAIL %d | SKIP %d |'
                   'WARN %d | INTERRUPT %s | CANCEL %s')
        results %= (results_data.get('pass', 0),
                    results_data.get('error', 0),
                    results_data.get('failures', 0),
                    results_data.get('skip', 0),
                    results_data.get('warn', 0),
                    results_data.get('interrupt', 0),
                    results_data.get('cancel', 0))
        self._print_job_details({'RESULTS': results})
        return exit_codes.AVOCADO_ALL_OK

    def run(self, config):
        results = {}

        jobs_dir = get_logs_dir()
        for result in glob(os.path.join(jobs_dir, '*/results.json')):
            with open(result, 'r') as fp:
                job = json.load(fp)
                results[job['job_id']] = result

        subcommand = config.get('jobs_subcommand')
        if subcommand == 'list':
            return self.handle_list_command(results)
        elif subcommand == 'show':
            return self.handle_show_command(config)
        elif subcommand == 'get-output-files':
            return self.handle_output_files_command(config)
        return exit_codes.AVOCADO_ALL_OK
