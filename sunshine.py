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
import datetime
import requests
import yaml
import decimal
from subprocess import call

# Load the config file.
CONFIG = yaml.safe_load(open('config.yml'))

def main():
    """The main program function."""
    if os.path.isfile(CONFIG['data_file']) is False:
        print CONFIG['data_file'] + " not found"
        sys.exit()

    # Make database connection variables available to all functions.
    global db
    global cursor

    # Open up a SQLite connection.
    try:
        db = sqlite3.connect('energy.db')
    except sqlite3.error, e:
        print "Count not connect to SQLite, with error %s:" % e.args[0]
        sys.exit(1)
    db.row_factory = dict_factory

    # Create a SQLite cursor.
    cursor = db.cursor()

    # See if the database table exists. If not, create it.
    cursor.execute("SELECT 1 \
                    FROM sqlite_master \
                    WHERE type='table' AND name='energy'")
    exists = cursor.fetchone()
    if exists is None:
        cursor.execute("CREATE TABLE energy(time INTEGER PRIMARY KEY NOT NULL, " \
            + "used INTEGER, generated INTEGER, label TEXT NULL, change TEXT NULL, "\
            + " temp_int INT NULL, temp_ext INT NULL")
        db.commit()

    # See when we last recorded power use data.
    cursor.execute("SELECT time \
                    FROM energy \
                    WHERE used IS NOT NULL \
                    ORDER BY time DESC \
                    LIMIT 1")
    last = cursor.fetchone()

    # Parse cron-retrieved records, providing the average amount of power used
    # in the prior two minutes. (The data file contains records with a
    # granularity that's much finer.)
    energy_data = dict()
    records = csvkit.CSVKitDictReader(open(CONFIG['data_file'], 'rb'))
    watts = []
    for record in records:
        if last['time'] >= int(round(float(record['src']))):
            continue
        energy_data['time'] = int(round(float(record['src'])))
        energy_data['using'] = int(record['ch1watts']) + int(record['ch2watts'])
        energy_data['temp_int'] = int(float(record['tmprF']))
        cursor.execute("INSERT INTO energy (time, used, temp_int) " \
                        + "VALUES(?, ?, ?)", \
                        (energy_data['time'], energy_data['using'], \
                        energy_data['temp_int']))
    db.commit()

    # Fetch Enphase data if the sun is shining.
    # QUICK HACK -- replace this with something way more elegant.
    from datetime import datetime, time
    now = datetime.now()
    now_time = now.time()
    if time(7,00) <= now.time() <= time(21,00):
        enphase_url = 'https://api.enphaseenergy.com/api/v2/systems/' \
            + CONFIG['enphase_system'] + '/summary?key=' + CONFIG['enphase_key'] \
            + '&user_id=' + CONFIG['enphase_user']
        response = urllib.urlopen(enphase_url)
        # We make this a global as a lousy hack to access it in export_json()
        global solar_data
        solar_data = json.loads(response.read())
        
        energy_data['generating'] = int(solar_data['current_power'])
        energy_data['generating_time'] = int(float(solar_data['last_report_at']))

        # See when we last recorded power generation data, to avoid duplicates.
        cursor.execute("SELECT time \
                        FROM energy \
                        WHERE generated IS NOT NULL \
                        ORDER BY time DESC \
                        LIMIT 1")
        last = cursor.fetchone()
        if last['time'] < energy_data['generating_time']:

            cursor.execute("INSERT INTO energy (time, generated) " \
                            + "VALUES(?, ?)", \
                            (energy_data['generating_time'], energy_data['generating']))
            db.commit()

    # Display the power use and generation data on the command line
    print energy_data

    # Save the last 12 hours of data as a JSON File.
    export_json()

    # Retrieve the most recent power data from the database.
    energy_data = get_current_status()

    # If we're generating >1kW of unused power.
    if (energy_data['generated'] - energy_data['used']) > 1000:

        # Provide a notification via Pushover.
        send_alert()

        # See if we've been generating >1kW for at least 20 minutes.
        cursor.execute("SELECT generated \
                        FROM energy \
                        WHERE time >= (strftime('%s','now') - (60*20) ) \
                        ORDER BY time DESC")
        records = cursor.fetchall()
        insufficient = False
        for record in records:
            if record['generated'] < 1000:
                insufficient = True
                break
        if insufficient == False:
            nest_set()

    db.close

def dict_factory(cursor, row):
    """Emit SQLite results as a dict."""
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

def get_current_status():
    """Retrieve the most recent generation and use data from the database."""
    status = {}
    cursor.execute("SELECT used \
                    FROM energy \
                    WHERE used IS NOT NULL \
                    ORDER BY time DESC \
                    LIMIT 1")
    record = cursor.fetchone()
    status['used'] = record['used']

    cursor.execute("SELECT generated \
                    FROM energy \
                    WHERE generated IS NOT NULL \
                    ORDER BY time DESC \
                    LIMIT 1")
    record = cursor.fetchone()
    status['generated'] = record['generated']

    return status

def export_json():
    """Store the past 12 hours of power use and generation data in a file."""
    cursor.execute("SELECT datetime(time, 'unixepoch', 'localtime') AS time, \
                    used, generated, label, change, temp_int \
                    FROM energy \
                    WHERE time >= (strftime('%s','now') - (60 * 60 * 12)) \
                    ORDER BY time DESC")
    records = cursor.fetchmany(10000)

    # Drop 90% of data points -- we have too many to chart reasonably. We
    # iterate through in reverse because deleting going in order while deleting
    # records makes a mess.
    num_records = len(records)
    i=0
    for j in range(num_records):
        reverse_i = num_records - 1 - j
        if records[reverse_i]['generated'] > 0:
            continue
        if i%9 != 0:
            records.pop(reverse_i)
        i+=1

    output = {}
    output['history'] = records
    
    output['today'] = daily_cumulative()
    try:
        solar_data['energy_today']
    except NameError:
        pass
    else:
        output['today']['generated'] = solar_data['energy_today']
    output['current'] = get_current_status()

    f = open(CONFIG['status_file'], 'w')
    f.write(json.dumps(output))
    f.close()

def send_alert():
    """Use Pushover to report excess wattage generation."""
    if not os.path.exists('.notified'):
        os.mknod('.notified')
    # No more than once per hour.
    if int(time.time()) - os.path.getmtime('.notified') > 3600:
        payload = {'token':  CONFIG['pushover_token'], \
            'user':   CONFIG['pushover_user'], \
            'title':  'Generating Excess Power', \
            'message': str(int(energy_data['generating']) - \
                int(energy_data['using'])) + ' excess watts.'}
        requests.post('https://api.pushover.net/1/messages.json', data=payload)
        os.utime('.notified', None)

def nest_status():
    """Get the status of the Nest."""

    # If it's cached, and the cached file is less than 30 minutes old, use it.
    if os.path.exists('.neststatus'):
        file_status = os.stat('.neststatus')
        if (int(time.time()) - file_status.st_mtime) < (60 * 30):
            return json.loads(open(filename).read(10000))

    # Retrieve status data from Nest.
    status = call(['nestcontrol/nest.py', '-u ' + CONFIG['nest']['username'] \
        + ' -p ' + CONFIG['nest']['password'] + ' -f'])
    status = json.loads(status)[0]['shared']
    return status

def nest_set():
    """Set the Nest temperature."""

    # Get the status of the Nest.
    status = nest_status()

    # If nobody's home, there's no sense in adjusting the temperature.
    if status['auto_away'] == 1:
        return False

    # If the current temperature settings are different than the excess-power
    # settings.
    if status['target_temperature_low'] != CONFIG['nest']['excess']['low'] and \
        status['target_temperature_high'] != CONFIG['nest']['excess']['high']:

        status = call(['nestcontrol/nest.py', '-u ' + CONFIG['nest']['username'] \
            + ' -p ' + CONFIG['nest']['password'] + ' ' \
            + CONFIG['nest']['excess']['low'] + '-' \
            + CONFIG['nest']['excess']['high']])


def daily_cumulative():
    """Calculate the cumulative power use and generation since midnight."""

    # Figure out the timestamp for midnight, when today started.
    today = datetime.date.today()
    d = datetime.datetime(today.year, today.month, today.day)
    midnight = int(decimal.Decimal(time.mktime(d.timetuple())).normalize())
    
    cursor.execute("SELECT time, used, generated \
                    FROM energy \
                    WHERE used IS NOT NULL AND time >= ?",
                    ((midnight,)))
    records = cursor.fetchall()

    prior = midnight
    used = []
    generated = []
    for record in records:
        duration = int(record['time'] - prior)
        used.append(int(record['used']) / int(60.0 * 60.0) / duration)
        prior = record['time']

    cumulative = {}
    cumulative['used'] = round(sum(used) / len(used) * 10000, 2)

    return cumulative

def label_use():
    """Label identifable draws on the power."""

    # Get power use over the past 120 minutes.
    cursor.execute("SELECT time, used, label \
                    FROM energy \
                    WHERE used IS NOT NULL \
                    AND time >= (strftime('%s','now') - (60 * 120) ) \
                    ORDER BY time ASC")
    records = cursor.fetchall()

    # If the difference between two points in time matches a device's draw,
    # record that fact in the database.
    prior = 0
    prior_2 = 0
    for record in records:
        if record['label'] is not None:
            prior_2 = prior
            prior = record['used']
            continue
        change = abs(record['used'] - prior)
        change_2 = abs(record['used'] - prior_2)
        for device in CONFIG['devices']:
            if (change >= device['watts']['low'] and change <= device['watts']['high']) or \
                (change_2 >= device['watts']['low'] and change_2 <= device['watts']['high']):
                if record['used'] > prior:
                   state = "on"
                else:
                   state = "off"
                cursor.execute("UPDATE energy \
                                SET label = ?, change = ? \
                                WHERE time = ?",
                                (device['name'], state, record['time']))
                db.commit()
                break
        prior_2 = prior
        prior = record['used']

    return True

if __name__ == "__main__":
    main()
    label_use()
