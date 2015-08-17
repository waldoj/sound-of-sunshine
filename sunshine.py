#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Adjusts my thermostat when my solar array is generating lots of power, and is
likely to continue.
"""

import csvkit
import os
import sys
import sqlite3
import urllib, json
import time
import requests
import yaml

# Load the config file.
CONFIG = yaml.safe_load(open('config.yml'))

def main():
    """The main program function."""
    if os.path.isfile(CONFIG['data_file']) is False:
        print CONFIG['data_file'] + " not found"
        sys.exit()

    # Parse cron-retrieved records, providing the average amount of power used
    # in the prior two minutes. (The data file contains records with a
    # granularity that's much finer.)
    energy_data = dict()
    records = csvkit.CSVKitDictReader(open(CONFIG['data_file'], 'rb'))
    watts = []
    for record in records:
        usage = int(record["ch1watts"]) + int(record["ch2watts"])
        watts.append(usage)
    energy_data['using'] = int(round(sum(watts) / len(watts)))

    # Fetch Enphase data
    enphase_url = 'https://api.enphaseenergy.com/api/v2/systems/' \
        + CONFIG['enphase_system'] + '/summary?key=' + CONFIG['enphase_key'] \
        + '&user_id=' + CONFIG['enphase_user']
    response = urllib.urlopen(enphase_url)
    solar_data = json.loads(response.read())
    energy_data['generating'] = int(solar_data["current_power"])

    # Display the power use and generation data on the command line
    print energy_data

    # Store the power use and generation data to SQLite.
    try:
        db = sqlite3.connect('energy.db')
    except sqlite3.error, e:
        print "Count not connect to SQLite, with error %s:" % e.args[0]
        sys.exit(1)

    # Create a SQLite cursor.
    cursor = db.cursor()

    # See if the database table exists. If not, create it.
    cursor.execute("SELECT 1 \
                    FROM sqlite_master \
                    WHERE type='table' AND name='energy'")
    exists = cursor.fetchone()
    if exists is None:
        cursor.execute("CREATE TABLE energy(time INTEGER PRIMARY KEY NOT NULL, " \
            + "used INTEGER, generated INTEGER)")
        db.commit()
    cursor.execute("INSERT INTO energy VALUES(?, ?, ?)", \
                    (int(time.time()), int(energy_data['using']), \
                    int(energy_data['generating'])))
    db.commit()

    # Store the past 12 hours of power use and generation data in a JSON file
    db.close()
    db = sqlite3.connect('energy.db')
    db.row_factory = dict_factory
    cursor = db.cursor()
    cursor.execute("SELECT datetime(time, 'unixepoch', 'localtime') AS time, \
                    used, generated \
                    FROM energy \
                    WHERE time >= (strftime('%s','now') - (60 * 60 * 12)) \
                    ORDER BY time DESC")
    records = cursor.fetchmany(360)
    records = list(reversed(records))
    f = open(CONFIG['status_file'], 'w')
    f.write(json.dumps(records))
    f.close()

    # If we're generating >1kW of unused power, reply.
    if (energy_data['generating'] - energy_data['using']) > 1000:

        # Send an alert, but no more often than hourly
        if not os.path.exists('.notified'):
            os.mknod('.notified')
        if int(time.time()) - os.path.getmtime('.notified') > 3600:
            payload = {'token':  CONFIG['pushover_token'], \
                'user':   CONFIG['pushover_user'], \
                'title':  'Generating Excess Power', \
                'message': str(int(energy_data['generating']) - \
                    int(energy_data['using'])) + ' excess watts.'}
            requests.post('https://api.pushover.net/1/messages.json', data=payload)
            os.utime('.notified', None)

def dict_factory(cursor, row):
    """Emit SQLite results as a dict."""
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

if __name__ == "__main__":
    main()
