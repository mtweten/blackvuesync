#!/usr/bin/env python3

# Copyright 2018 Alessandro Colomba (https://github.com/acolomba)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
# persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
# Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import argparse
import datetime
from collections import namedtuple
import re
import os
import urllib
import urllib.parse
import urllib.request


# represents a recording: filename and metadata
Recording = namedtuple('Recording', 'filename datetime type direction extension')

# indicator that we're doing a dry run
dry_run = None

# keep and cutoff date; only recordings from this date on are downloaded and kept
keep_re = re.compile(r"""(?P<range>\d+)(?P<unit>[dw]?)""", re.VERBOSE)
cutoff_date = None


def calc_cutoff_date(keep):
    keep_match = re.fullmatch(keep_re, keep)

    if keep_match is None:
        raise Exception("KEEP must be in the format <number>[dw]")

    keep_range = int(keep_match.group("range"))

    if keep_range < 1:
        raise Exception("KEEP must be greater than one.")

    keep_unit = keep_match.group("unit") or "d"

    today = datetime.date.today()

    if keep_unit == "d" or keep_unit is None:
        keep_range_timedelta = datetime.timedelta(days=keep_range)
    elif keep_unit is "w":
        keep_range_timedelta = datetime.timedelta(weeks=keep_range)
    else:
        # this indicates a coding error
        raise Exception("unknown KEEP unit : %s" % keep_unit)

    return today - keep_range_timedelta


filename_re = re.compile(r"""(?P<year>\d\d\d\d)(?P<month>\d\d)(?P<day>\d\d)
    _(?P<hour>\d\d)(?P<minute>\d\d)(?P<second>\d\d)
    _(?P<type>[NEPM])
    (?P<direction>[FR])
    \.(?P<extension>\w+)""", re.VERBOSE)


def get_recording(filename):
    """extracts recording information from a filename"""
    filename_match = re.fullmatch(filename_re, filename)

    if filename_match is None:
        return None

    year = int(filename_match.group("year"))
    month = int(filename_match.group("month"))
    day = int(filename_match.group("day"))
    hour = int(filename_match.group("hour"))
    minute = int(filename_match.group("minute"))
    second = int(filename_match.group("second"))
    recording_datetime = datetime.datetime(year, month, day, hour, minute, second)

    recording_type = filename_match.group("type")
    recording_direction = filename_match.group("direction")
    recording_extension = filename_match.group("extension")

    return Recording(filename, recording_datetime, recording_type, recording_direction, recording_extension)


# pattern of a recording filename as returned in each line from from the dashcam index page
file_line_re = re.compile(r"n:/Record/(?P<filename>.*\.mp4),s:1000000\r\n")


def get_filenames(file_lines):
    """extracts the recording filenames from the lines returned by the dashcam index page"""
    filenames = []
    for file_line in file_lines:
        file_line_match = re.fullmatch(file_line_re, file_line)
        # the first line is "v:1.00", which won't match, so we skip it
        if file_line_match:
            filenames.append(file_line_match.group("filename"))

    return filenames


def get_dashcam_filenames(base_url):
    """gets the recording filenames from the dashcam at the """
    try:
        url = urllib.parse.urljoin(base_url, "blackvue_vod.cgi")
        request = urllib.request.Request(url)
        response = urllib.request.urlopen(request)
    except urllib.error.URLError as e:
        raise Exception("Cannot communicate with dashcam at address : %s; error : %s" % (base_url, e))

    response_status_code = response.getcode()
    if response_status_code != 200:
        raise Exception("Error response from : %s ; status code : %s" % (base_url, response_status_code))

    charset = response.info().get_param('charset', 'UTF-8')
    file_lines = [x.decode(charset) for x in response.readlines()]

    return get_filenames(file_lines)


def download_file(base_url, filename, destination):
    """downloads a file from the dashcam to the destination directory"""
    global dry_run

    filepath = os.path.join(destination, filename)

    if os.path.exists(filepath):
        print("Already downloaded : %s" % filename)
        return

    temp_filepath = os.path.join(destination, ".%s" % filename)
    if os.path.exists(temp_filepath):
        print("Found unfinished download : %s" % temp_filepath)

    url = urllib.parse.urljoin(base_url, "Record/%s" % filename)
    if not dry_run:
        print("Downloading : %s; to : %s..." % (filename, filepath), end="", flush=True)
        urllib.request.urlretrieve(url, temp_filepath)
        os.rename(temp_filepath, filepath)
        print("done.")
    else:
        print("Dry run: would download : %s; to : %s" % (filename, filepath))


def download_recording(base_url, recording, destination):
    """downloads the set of recordings, including gps data, for the given filename from the dashcam to the destination
    directory"""
    filename = recording.filename
    download_file(base_url, filename, destination)

    # only normal recordings have gps data
    if filename.endswith("_NF.mp4"):
        base_filename = filename[:-7]

        gps_filename = "%s_N.gps" % base_filename
        download_file(base_url, gps_filename, destination)

        tgf_filename = "%s_N.3gf" % base_filename
        download_file(base_url, tgf_filename, destination)


def get_destination_recordings(destination):
    """reads files from the destination directory and returns them as recording structures"""
    existing_files = os.listdir(destination)

    return [x for x in [get_recording(x) for x in existing_files] if x is not None]


def get_outdated_recordings(recordings):
    """returns the recordings that are prior to the cutoff date"""
    global cutoff_date

    return [] if cutoff_date is None else [x for x in recordings if x.datetime.date() < cutoff_date]


def get_current_recordings(recordings):
    """returns the recordings that are after or on the cutoff date"""
    global cutoff_date
    return recordings if cutoff_date is None else [x for x in recordings if x.datetime.date() >= cutoff_date]


def prepare_destination(destination):
    """prepares the destination, esuring it's valid and removing excess recordings"""
    global dry_run
    global cutoff_date

    # if no destination, creates it
    if not os.path.exists(destination):
        os.makedirs(destination)
        return

    # destination exists, tests if directory
    if not os.path.isdir(destination):
        raise Exception("destination is not a directory : %s" % destination)

    # destination is a directory, tests if writable
    if not os.access(destination, os.W_OK):
        raise Exception("destination directory not writable : %s" % destination)

    if cutoff_date:
        existing_recordings = get_destination_recordings(destination)
        outdated_recordings = get_outdated_recordings(existing_recordings)

        for outdated_recording in outdated_recordings:
            outdated_filepath = os.path.join(destination, outdated_recording.filename)
            if not dry_run:
                    os.remove(outdated_filepath)
            else:
                print("Would remove : %s" % outdated_filepath)


def sync(address, destination):
    """synchronizes the recordings at the dashcam address with the destination directory"""
    base_url = "http://%s" % address
    dashcam_filenames = get_dashcam_filenames(base_url)
    dashcam_recordings = [get_recording(x) for x in dashcam_filenames]
    current_dashcam_recordings = get_current_recordings(dashcam_recordings)

    for recording in current_dashcam_recordings:
        download_recording(base_url, recording, destination)


def parse_args():
    """parses the command-line arguments"""
    arg_parser = argparse.ArgumentParser(description="Synchronizes BlackVue dashcam recordings with a local directory.",
                                         epilog="Bug reports: https://github.com/acolomba/BlackVueSync")
    arg_parser.add_argument("address", metavar="ADDRESS",
                            help="dashcam IP address or name")
    arg_parser.add_argument("-d", "--destination", metavar="DEST",
                            help="destination directory (defaults to c  urrent directory)")
    arg_parser.add_argument("-k", "--keep", metavar="KEEP_RANGE",
                            help="keeps recordings in the given range, removing the rest; defaults to days, but can suffix with d, w for days or weeks respectively)")
    arg_parser.add_argument("--dry-run", help="shows what the program would do", action='store_true')

    return arg_parser.parse_args()


def run():
    # dry-run is a global setting
    global dry_run
    global cutoff_date

    args = parse_args()

    dry_run = args.dry_run
    if args.keep:
        cutoff_date = calc_cutoff_date(args.keep)
        print("Cutoff date : %s" % cutoff_date)

    try:
        # prepares the local file destination
        destination = args.destination or os.getcwd()
        prepare_destination(destination)

        sync(args.address, destination)
    except Exception as e:
        print(e)
        return 1


if __name__ == "__main__":
    run()
