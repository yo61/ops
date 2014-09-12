#!/usr/bin/env python
#
# Copyright (c) 2014 Vincent Janelle <randomfrequency@gmail.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# Tiny bits of logic taken from Cloudcaster.py, @WrathOfChris
#

import argparse
import boto
import boto.ec2
import boto.ec2.autoscale
import boto.ec2.elb
import boto.route53
import boto.route53.zone
import boto.vpc
import datetime
import json
import yaml
import os
import re
import sys
import time
import pprint

from operator import itemgetter, attrgetter

MAX_COUNT=5
pp = pprint.PrettyPrinter(indent=4)

if 'AWS_ACCESS_KEY' in os.environ:
  aws_key = os.environ['AWS_ACCESS_KEY']
else:
  aws_key = None
if 'AWS_SECRET_KEY' in os.environ:
  aws_secret = os.environ['AWS_SECRET_KEY']
else:
  aws_secret = None

vpc_subnetids = []
vpc_pubsubnetids = []
nat_subnetidx = 0
nat_instances = []
nat_instwait = 5
nat_publicdns = None

parser = argparse.ArgumentParser(description="Remove stale AWS launch configurations generated by cloudcaster.py")

parser.add_argument("-v", "--verbose", help="verbosity", action="store_true")
parser.add_argument("-n", "--dry-run", help="Dry run, noop mode", action="store_true")
parser.add_argument("-c", "--count", help="max count", type=int)
parser.add_argument("-s", "--sleep", help="millisec to delay vs rate limit throttling", type=int)
parser.add_argument("file", help="cloudcaster file")

args = parser.parse_args()
if args.file == None:
  parser.print_help()
  sys.exit(1)

verbose = args.verbose
dry_run = args.dry_run

if args.count:
    MAX_COUNT=args.count
if args.sleep:
    SLEEP_MS=args.sleep
conffile = open(args.file).read()
if args.file.lower().endswith(".yaml"):
    conf = yaml.load(conffile)
else:
    conf = json.loads(conffile)

# SETUP BOTO
awsasg = boto.ec2.autoscale.connect_to_region(conf['aws']['region'], aws_access_key_id=aws_key, aws_secret_access_key=aws_secret)

lc_groups = {}

if dry_run:
    print "DRY RUN - NOT EXECUTING CLEANUP"

# Utility function to extract name and date from name
def extract_lc_names(config):
    # name-"%Y%m%d%H%M%S"
    match = re.search("(.+)-(\d{14})", config.name)
    if match:
        # "%Y%m%d%H%M%S"
        date = time.strptime(match.group(2), "%Y%m%d%H%M%S")
        return [ match.group(1), date ]

# Scroll through all launch configurations to cache configurations
def get_launch_configurations():
  res = []
  lcs = awsasg.get_all_launch_configurations()
  for l in lcs:
    res.append(l)

  while lcs.next_token != None:
    lcs = awsasg.get_all_launch_configurations(next_token=lcs.next_token)
    for l in lcs:
      res.append(l)

  return res

# Hash of launch configuration names -> [ time.time_struct, ... ]
lcname_to_date = {}

def keyitup(entry):
    lc_name = entry[0]
    lc_date = entry[1]

    # If configration already has a list going append
    # otherwise create a new one..
    if lc_name in lcname_to_date:
        lcname_to_date[lc_name].append(lc_date)
    else:
        lcname_to_date[lc_name] = [ lc_date ]

# Begin work - retrieve all launch configurations

lc = get_launch_configurations()

# sort the list of configurations by name -> [ time.time_struct, ... ] in 
# order from newwest to oldest
lc_groups = sorted(list(map(extract_lc_names, lc)), key=itemgetter(0,1), reverse=False)

map(keyitup, lc_groups)

for lc_name in lcname_to_date:
    count = len(lcname_to_date[lc_name])
    if count > MAX_COUNT:

        for i in range(0,count - MAX_COUNT):
            lc = "%s-%s" % ( lc_name, time.strftime("%Y%m%d%H%M%S", lcname_to_date[lc_name][i]) )
            if verbose:
                print "Pruning %s" % ( lc )
            if dry_run:
                print "-> WOULD DELETE %s" % ( lc )
            else:
                res = awsasg.delete_launch_configuration(lc)
            if args.sleep:
              time.sleep(float(SLEEP_MS/1000))

