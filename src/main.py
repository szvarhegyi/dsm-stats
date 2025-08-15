import os
from fastapi import FastAPI
from dotenv import load_dotenv
import requests
from pysnmp.hlapi.v3arch.asyncio import *
import asyncio
import json
import time
import datetime
from zoneinfo import ZoneInfo


app = FastAPI()
load_dotenv()

DSM_HOST = os.getenv('DSM_HOST')
USERNAME = os.getenv('DSM_USERNAME')
PASSWORD = os.getenv('DSM_PASSWORD')

SNMP_HOST = os.getenv('SNMP_HOST')
SNMP_USERNAME = os.getenv('SNMP_USERNAME')
SNMP_PASSWORD = os.getenv('SNMP_PASSWORD')

JSON_SECRET = os.getenv('JSON_SECRET')
SLEEP_TIME = int(os.getenv('SLEEP_TIME', 60))

JSONBIN_SERVER = os.getenv('JSONBIN_SERVER')

if(os.getenv('INFLUX_ENABLED') == 'true' or os.getenv('INFLUX_ENABLED') == 'True')
    INFLUX_ENABLED = True
else:
    INFLUX_ENABLED = False
    
INFLUX_URL = os.getenv('INFLUX_URL')
INFLUX_TOKEN = os.getenv('INFLUX_TOKEN')
INFLUX_ORG = os.getenv('INFLUX_ORG')
INFLUX_BUCKET = os.getenv('INFLUX_BUCKET')

def get_sid():
    url = f"{DSM_HOST}/webapi/auth.cgi"
    params = {
        "api": "SYNO.API.Auth",
        "version": "6",
        "method": "login",
        "account": USERNAME,
        "passwd": PASSWORD,
        "session": "DownloadStation",
        "format": "sid"
    }
    r = requests.get(url, params=params, verify=False)
    r.raise_for_status()
    
    return r.json()["data"]["sid"]

def get_system_temp(sid):
    url = f'{DSM_HOST}/webapi/entry.cgi'
    payload = {
        'api': 'SYNO.Core.System',
        'method': 'info',
        'version': '1',
        "_sid": sid
    }
    r = requests.post(url, data=payload, verify=False)
    r.raise_for_status()
    body = r.json()

    return body.get('data', {}).get('sys_temp', 0)

async def get_disk_temps(ipaddress, username, passwd):
    temps = {}
    oids = [
        ObjectType(ObjectIdentity('1.3.6.1.4.1.6574.2.1.1.2')),
        ObjectType(ObjectIdentity('1.3.6.1.4.1.6574.2.1.1.3')),
        ObjectType(ObjectIdentity('1.3.6.1.4.1.6574.2.1.1.6'))
    ]

    errorIndication, errorStatus, errorIndex, varBinds = await bulk_cmd(
        SnmpEngine(),
        UsmUserData(username, passwd, authProtocol=usmHMACSHAAuthProtocol),
        await UdpTransportTarget.create((ipaddress, 161)),
        ContextData(),
        0, 10,
        *oids
    )

    if errorIndication or errorStatus:
        print("SNMP hiba:", errorIndication or errorStatus.prettyPrint())
        return temps

    disk_data = {}
    for varBind in varBinds:
        oid, value = varBind
        oid_str = str(oid)

        index = oid_str.split('.')[-1]
        if index not in disk_data:
            disk_data[index] = {}

        if oid_str.startswith('1.3.6.1.4.1.6574.2.1.1.2'):
            disk_data[index]['name'] = str(value)
        elif oid_str.startswith('1.3.6.1.4.1.6574.2.1.1.3'):
            disk_data[index]['model'] = str(value)
        elif oid_str.startswith('1.3.6.1.4.1.6574.2.1.1.6'):
            disk_data[index]['temperature'] = int(value)

    for index, info in disk_data.items():
        temps[f'disk{index}'] = info.get('temperature', 'Unknown')

    temps['alldata'] = disk_data
    
    return temps

def send_data_to_influxdb(result):
    lines = []
    for key,value in result.items():
        if(key not in ['alldata', 'date']):
            lines.append(
                f"nas_temps,host=nas1,type={key} value={value}i {int(datetime.datetime.now(ZoneInfo('Europe/Budapest')).timestamp()*1e9)}"
            )
    payload = "\n".join(lines)
    
    resp = requests.post(
        f"{INFLUX_URL}/api/v2/write?org={INFLUX_ORG}&bucket={INFLUX_BUCKET}&precision=ns",
        headers={
            "Authorization": f"Token {INFLUX_TOKEN}",
            "Content-Type": "text/plain"
        },
        data=payload
    )

def send_data():
    result = asyncio.run(get_disk_temps(SNMP_HOST, SNMP_USERNAME, SNMP_PASSWORD))
    x = datetime.datetime.now(ZoneInfo("Europe/Budapest"))
    result['cpu'] = get_system_temp(get_sid())
    result['date'] = x.strftime("%Y-%m-%d %H:%M:%S")
    
    url = JSONBIN_SERVER + "/bins/" + JSON_SECRET

    payload = json.dumps(result)

    headers = {
    'Content-Type': 'application/json'
    }

    if(INFLUX_ENABLED):
        send_data_to_influxdb(result)
    
    response = requests.request("POST", url, headers=headers, data=payload)

if __name__ == "__main__":
    while True:
        print("send data")
        send_data()
        time.sleep(SLEEP_TIME)