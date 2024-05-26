"""Sensor platform for the DKB integration."""
import getpass
import logging
from collections import deque
from datetime import datetime as DT, timedelta
import xml.etree.ElementTree as ET
import voluptuous as vol

from fints.client import FinTS3PinTanClient, NeedTANResponse
from fints.utils import minimal_interactive_cli_bootstrap

from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import HomeAssistantType, ConfigType, AddEntitiesCallback
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "dkb"

CONF_BLZ = "blz"
CONF_IBAN = "iban"

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BLZ): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_IBAN): cv.string,
        vol.Optional(CONF_NAME, default=DOMAIN): cv.string,
    }
)

def setup_platform(hass: HomeAssistantType, config: ConfigType, add_entities: AddEntitiesCallback, discovery_info=None):
    """Set up the sensor platform."""
    blz = config.get(CONF_BLZ)
    login = config.get(CONF_USERNAME)
    pin = config.get(CONF_PASSWORD)
    iban = config.get(CONF_IBAN)
    name = config.get(CONF_NAME)

    # FinTS3PinTanClient initialisieren
    f = FinTS3PinTanClient(
        blz,
        login,
        pin,
        'https://banking-dkb.s-fints-pt-dkb.de/fints30',
        product_id='6151256F3D4F9975B877BD4A2'
    )

    minimal_interactive_cli_bootstrap(f)

    with f:
        if f.init_tan_response:
            ask_for_tan(f.init_tan_response, f)

        accounts = f.get_sepa_accounts()
        if isinstance(accounts, NeedTANResponse):
            accounts = ask_for_tan(accounts, f)

        if iban:
            accounts = [acc for acc in accounts if acc.iban == iban]

        sensors = []
        for i, account in enumerate(accounts):
            setup_account(hass, f, account, sensors, name, i)

    add_entities(sensors)

def setup_account(hass, f, account, sensors, name, index):
    """Set up devices and entities for a single account."""
    iban = account.iban

    # Erstelle Geräte
    hass.data[DOMAIN][f"{name}_summary_{iban}"] = hass.data[DOMAIN].get(f"{name}_summary_{iban}", {})
    hass.data[DOMAIN][f"{name}_activities_{iban}"] = hass.data[DOMAIN].get(f"{name}_activities_{iban}", {})

    # Erstelle Entitäten für Kontostand
    hass.data[DOMAIN][f"{name}_summary_{iban}"]["account_balance_booked"] = get_balance(account, f)
    hass.data[DOMAIN][f"{name}_summary_{iban}"]["account_balance_with_pending"] = get_balance_with(account, f)

    sensors.append(DKBBalanceSensor(f"{name}_summary_{iban}", "account_balance_booked", hass.data[DOMAIN][f"{name}_summary_{iban}"]["account_balance_booked"]))
    sensors.append(DKBBalanceSensor(f"{name}_summary_{iban}", "account_balance_with_pending", hass.data[DOMAIN][f"{name}_summary_{iban}"]["account_balance_with_pending"]))

    # Erstelle Entitäten für letzte 10 Transaktionen
    transactions = get_last_10_transactions(account, f)
    for i, transaction in enumerate(transactions):
        hass.data[DOMAIN][f"{name}_activities_{iban}"][f"activity_{i}"] = transaction
        sensors.append(DKBActivitySensor(f"{name}_activities_{iban}", f"activity_{i}", transaction))

def ask_for_tan(response, f):
    """Funktion zum Abfragen der TAN."""
    print("A TAN is required")
    print(response.challenge)
    tan = input('Please enter TAN:')
    return f.send_tan(response, tan)

def calculate_balance(nested_xml_data):
    """Berechnet den Kontostand basierend auf dem übergebenen XML-Daten."""
    namespace = {'ns': 'urn:iso:std:iso:20022:tech:xsd:camt.052.001.02'}
    balance = 0
    for xml_data_tuple in nested_xml_data:
        for xml_data_bytes in xml_data_tuple:
            if xml_data_bytes is not None:
                root = ET.fromstring(xml_data_bytes)
                balance_amount_element = root.find('.//ns:Bal/ns:Amt', namespace)
                if balance_amount_element is not None:
                    balance = float(balance_amount_element.text)
                else:
                    print("Hier keine Balance")

                # Berechne die Summe der geplanten Buchungen
                pending_entries = root.findall('.//ns:Ntry[ns:Sts="PDNG"]', namespace)
                for entry in pending_entries:
                    amount = float(entry.find('ns:Amt', namespace).text)
                    indicator = entry.find('ns:CdtDbtInd', namespace).text
                    if indicator == 'DBIT':
                        balance = balance - amount
                    elif indicator == 'CRDT':
                        balance = balance + amount
    return round(balance, 2)

def get_balance(account, f):
    """Funktion zum Abrufen des gebuchten Kontostands."""
    res = f.get_balance(account)
    while isinstance(res, NeedTANResponse):
        res = ask_for_tan(res, f)
    return res

def get_balance_with(account, f):
    """Funktion zum Abrufen des Kontostands inklusive ausstehender Buchungen."""
    res = f.get_transactions_xml(account, DT.today() - timedelta(days=0), DT.today())
    while isinstance(res, NeedTANResponse):
        res = ask_for_tan(res, f)
    balance = calculate_balance(res)
    return balance

def get_last_10_transactions(account, f):
    """Funktion zum Abrufen der letzten 10 Transaktionen."""
    transactions = deque(maxlen=10)
    i = 0
    while len(transactions) + 1 != transactions.maxlen:
        i += 60
        if i == 60:
            res = f.get_transactions_xml(account, DT.today() - timedelta(days=i), DT.today() - timedelta(days=i - 60))
        else:
            res = f.get_transactions_xml(account, DT.today() - timedelta(days=i), DT.today() - timedelta(days=i - 59))

        while isinstance(res, NeedTANResponse):
            res = ask_for_tan(res, f)
        namespace = {'ns': 'urn:iso:std:iso:20022:tech:xsd:camt.052.001.02'}

        for xml_data_tuple in res:
            for xml_data_bytes in xml_data_tuple:
                if xml_data_bytes is not None:
                    root = ET.fromstring(xml_data_bytes)
                    entries = root.findall('.//ns:Ntry', namespace)
                    for entry in entries:
                        amount = entry.find('ns:Amt', namespace).text
                        indicator = entry.find('ns:CdtDbtInd', namespace).text
                        if indicator == 'DBIT':
                            amount = float('-' + amount)
                        else:
                            amount = float('+' + amount)
                        status = entry.find('ns:Sts', namespace).text
                        booking_date = entry.find('ns:BookgDt/ns:Dt', namespace).text
                        valuation_date = entry.find('ns:ValDt/ns:Dt', namespace)
                        if valuation_date is not None:
                            valuation_date = valuation_date.text
                        details = entry.find('ns:NtryDtls/ns:TxDtls', namespace)
                        recording_time = details.find('ns:Refs/ns:Prtry/ns:Ref', namespace)
                        if recording_time is not None:
                            recording_time = recording_time.text
                            datetime_obj = DT.strptime(recording_time, '%Y-%m-%d-%H.%M.%S.%f')
                            recording_time = datetime_obj.strftime('%d.%m.%Y %H:%M:%S')
                        else:
                            recording_time = None
                        sendername = details.find('ns:RltdPties/ns:Dbtr/ns:Nm', namespace)
                        if sendername is not None:
                            if sendername.text == 'ISSUER':
                                sendername = 'Justin Hahn'
                            else:
                                sendername = sendername.text
                        else:
                            sendername = 'DKB'
                        receivername = details.find('ns:RltdPties/ns:Cdtr/ns:Nm', namespace)
                        if receivername is not None:
                            receivername = receivername.text
                        else:
                            receivername = 'Justin Hahn'

                        comment = details.find('ns:RmtInf/ns:Ustrd', namespace)
                        if comment is not None:
                            comment = comment.text
                        else:
                            comment = None

                        transaction = {
                            'amount': amount,
                            'status': status,
                            'booking_date': booking_date,
                            'valuation_date': valuation_date,
                            'recording_time': recording_time,
                            'comment': comment,
                            'sendername': sendername,
                            'receivername': receivername
                        }

                        transactions.append(transaction)

    transactions_list = list(transactions)
    for transaction in transactions_list:
        if transaction['recording_time']:
            transaction['recording_time'] = DT.strptime(transaction['recording_time'], '%d.%m.%Y %H:%M:%S')
    transactions_list.sort(key=lambda x: x['recording_time'])
    for transaction in transactions_list:
        if transaction['recording_time']:
            transaction['recording_time'] = transaction['recording_time'].strftime('%d.%m.%Y %H:%M:%S')
    print("letzte 10 Rekorde erfolgreich abgerufen")
    return list(transactions_list)


class DKBBalanceSensor(Entity):
    """Representation of a DKB account balance sensor."""

    def __init__(self, device_id, entity_id, entity_data):
        """Initialize the sensor."""
        self._device_id = device_id
        self._entity_id = entity_id
        self._balance = entity_data

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_id} {self._entity_id}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._balance

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return "€"

class DKBActivitySensor(Entity):
    """Representation of a DKB account activity sensor."""

    def __init__(self, device_id, entity_id, entity_data):
        """Initialize the sensor."""
        self._device_id = device_id
        self._entity_id = entity_id
        self._activity = entity_data

    @property
    def name(self):
        """Return the name of the sensor."""
        return f"{self._device_id} {self._entity_id}"

    @property
    def state(self):
        """Return the state of the sensor."""
        return f"{self._activity['amount']} - {self._activity['comment']}"

    @property
    def device_class(self):
        """Return the device class of this entity."""
        return "monetary"

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return "€"

    @property
    def device_state_attributes(self):
        """Return the state attributes of the sensor."""
        return self._activity
 