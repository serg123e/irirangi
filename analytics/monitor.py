import requests
from xml.etree import ElementTree as ET
import os
from datetime import datetime
import pytz


import time


def get_listeners():
    url = os.environ.get('ICECAST_STATS_URL') # 'http://localhost:8000/admin/stats'
    username = os.environ.get('ICECAST_ADMIN_USERNAME')
    password = os.environ.get('ICECAST_ADMIN_PASSWORD')
    response = requests.get(url, auth=(username, password))
    xml = ET.fromstring(response.content)
    listeners = xml.find('./listeners')
    return listeners.text





# Set the desired timezone
timezone = pytz.timezone('Europe/Madrid')

while True:
    listeners = get_listeners()

    current_time = datetime.now().astimezone(timezone).strftime("%Y-%m-%d %H:%M")
    with open('/logs/visitors.csv', 'a') as f:
        f.write(current_time + "\t" + listeners + "\n")

    time.sleep(60)

