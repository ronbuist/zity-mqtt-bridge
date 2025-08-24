import yaml
import time
import json
import threading
import logging
import paho.mqtt.client as mqtt
from pymodbus.client.serial import ModbusSerialClient

# ------------------ Lock ----------------------#
state_lock = threading.Lock()

# ------------------ Load Config ------------------ #
with open("zity_config.yaml", "r") as f:
    config = yaml.safe_load(f)

base_topic = config["mqtt"]["base_topic"]
zones = config["zones"]
trigger_register = config["trigger_register"]
system_registers = config["system_registers"]
alarm_registers = {int(k): v for k, v in config["alarm_registers"].items()}
system_mode_write_register = system_registers["mode_write"]
system_power_mode_write_register = system_registers["power_mode_write"]
slave_id = config["modbus"]["slave_id"]
overall_status_register = system_registers["mode"]
master_zone = config["master_zone"]
loglevel = config["loglevel"]
latency = config["latency"]

# ------------------ Logging Setup ------------------ #
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger().setLevel(loglevel.upper())

# ------------------ Manual Override State ------------------ #
manual_override_states = {zone_id: False for zone_id in zones}
last_mqtt_values = {
    zone_id: {
        'reset': False,
        'postpone': 0,
        'temp': None,
        'mode': None,
        'fan_mode': None,
        'preset_mode': None
    } for zone_id in zones
}
first_poll_completed = False

# ----------------- MQTT Discovery device structures ----------- #
ZONE_DEVICE_INFO = lambda zid, zname: {
    "identifiers": [f"zity_zone_{zid}"],
    "name": f"Zity Zone {zname}",
    "manufacturer": "Madel",
    "model": "Zity 2.0",
    "via_device": "zity_controller"
}

SYSTEM_DEVICE_INFO = {
    "identifiers": ["zity_controller"],
    "name": "Zity Controller",
    "manufacturer": "Madel",
    "model": "Zity 2.0"
}

# ------------------ Status lists ------------------- #
state_list = ["off", "heat", "heat", "cool", "cool", "", "", "", "", "fan_only", "", "", "", "", "dry"]
fan_mode_list = ["auto", "low", "medium", "high"]
system_fan_mode_list = ["off", "low", "medium", "high", "very high"]

# ------------------ Modbus client ------------------ #
mb = ModbusSerialClient(
    method=config["modbus"]["method"],
    port=config["modbus"]["port"],
    baudrate=config["modbus"]["baudrate"],
    stopbits=config["modbus"]["stopbits"],
    bytesize=config["modbus"]["bytesize"],
    parity=config["modbus"]["parity"],
    timeout=1
)

# ------------------ MQTT Discovery helpers ------------------ #

def publish_discovery(zone_id):
    zone = zones[zone_id]
    topic_prefix = f"homeassistant/climate/zity_zone_{zone_id}"
    object_id = f"zity_zone_{zone_id}"
    name = "Airco " + zone["name"]

    climate_config = {
        "name": name,
        "unique_id": object_id,
        "mode_command_topic": f"{base_topic}/zone/{zone_id}/set_mode",
        "mode_state_topic": f"{base_topic}/zone/{zone_id}/mode",
        "temperature_command_topic": f"{base_topic}/zone/{zone_id}/set_temp",
        "temperature_state_topic": f"{base_topic}/zone/{zone_id}/setpoint",
        "current_temperature_topic": f"{base_topic}/zone/{zone_id}/temp",
        "fan_mode_command_topic": f"{base_topic}/zone/{zone_id}/set_fan_mode",
        "fan_mode_state_topic": f"{base_topic}/zone/{zone_id}/fan_mode",
        "preset_mode_command_topic": f"{base_topic}/zone/{zone_id}/set_preset_mode",
        "preset_mode_state_topic": f"{base_topic}/zone/{zone_id}/preset_mode",
        "min_temp": 16.5,
        "max_temp": 30,
        "temp_step": 0.5,
        "modes": ["off", "cool", "heat", "dry", "fan_only"],
        "preset_modes": ["eco"],
        "precision": 0.1,
        "optimistic": "true",
        "icon": "mdi:air-conditioner",
        "qos": 0
    }
    climate_config["device"] = ZONE_DEVICE_INFO(zone_id, zone["name"])

    client.publish(f"{topic_prefix}/config", json.dumps(climate_config), retain=True)

    damper_config = {
        "name": f"{zone['name']} Damper",
        "unique_id": f"zity_zone_{zone_id}_damper",
        "state_topic": f"{base_topic}/zone/{zone_id}/damper_status",
        "payload_on": "open",
        "payload_off": "closed",
        "device_class": "running"
    }
    damper_config["device"] = ZONE_DEVICE_INFO(zone_id, zone["name"])
    client.publish(f"homeassistant/binary_sensor/zity_zone_{zone_id}_damper/config", json.dumps(damper_config), retain=True)

    current_temp_config = {
        "name" : f"{zone['name']} Current Temperature",
        "unique_id": f"zity_zone_{zone_id}_current_temperature",
        "state_topic": f"{base_topic}/zone/{zone_id}/temp",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
    }
    current_temp_config["device"] = ZONE_DEVICE_INFO(zone_id, zone["name"])
    client.publish(f"homeassistant/sensor/zity_zone_{zone_id}_current_temperature/config", json.dumps(current_temp_config), retain=True)

    setpoint_config = {
        "name" : f"{zone['name']} Setpoint",
        "unique_id": f"zity_zone_{zone_id}_setpoint",
        "state_topic": f"{base_topic}/zone/{zone_id}/setpoint",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
    }
    setpoint_config["device"] = ZONE_DEVICE_INFO(zone_id, zone["name"])
    client.publish(f"homeassistant/sensor/zity_zone_{zone_id}_setpoint/config", json.dumps(setpoint_config), retain=True)

    # Manual Override Switch Discovery
    manual_override_config = {
        "name": f"{zone['name']} Manual Override",
        "unique_id": f"zity_zone_{zone_id}_manual_override",
        "state_topic": f"{base_topic}/zone/{zone_id}/manual_override",
        "command_topic": f"{base_topic}/zone/{zone_id}/set_manual_override",
        "payload_on": "ON",
        "payload_off": "OFF",
        "state_on": "ON",
        "state_off": "OFF",
        "icon": "mdi:account-edit",
        "optimistic": "false"
    }
    manual_override_config["device"] = ZONE_DEVICE_INFO(zone_id, zone["name"])
    client.publish(f"homeassistant/switch/zity_zone_{zone_id}_manual_override/config", json.dumps(manual_override_config), retain=True)

def publish_system_discovery():
    for key in system_registers:
        topic = f"homeassistant/sensor/zity_system_{key}/config"
        if "write" in key:
            continue
        config_payload = {
            "name": f"Zity System {key.replace('_', ' ').title()}",
            "unique_id": f"zity_system_{key}",
            "state_topic": f"{base_topic}/system/{key}",
        }
        if "temp" in key or "setpoint" in key:
            config_payload["unit_of_measurement"] = "°C"
        if key == "power_mode":
            config_payload["command_topic"] = f"{base_topic}/system/set_power"
            config_payload["payload_on"] = "on"
            config_payload["payload_off"] = "off"
            topic = f"homeassistant/switch/zity_system_{key}/config"
        if key == "controller_mode":
            config_payload["payload_on"] = "on"
            config_payload["payload_off"] = "off"
            topic = f"homeassistant/binary_sensor/zity_system_{key}/config"
        config_payload["device"] = SYSTEM_DEVICE_INFO
        client.publish(topic, json.dumps(config_payload), retain=True)

    select_config = {
        "name": "Zity System Mode",
        "unique_id": "zity_system_mode_select",
        "command_topic": f"{base_topic}/system/set_mode",
        "state_topic": f"{base_topic}/system/mode",
        "options": ["off", "cool", "heat", "dry", "fan_only"],
        "optimistic": "true"
    }
    select_config["device"] = SYSTEM_DEVICE_INFO
    client.publish("homeassistant/select/zity_system_mode/config", json.dumps(select_config), retain=True)

    for reg, name in alarm_registers.items():
        config = {
            "name": f"Zity Alarm – {name.replace('_', ' ').title()}",
            "unique_id": f"zity_alarm_{name}",
            "state_topic": f"{base_topic}/system/alarm_{reg}",
            "payload_on": "1",
            "payload_off": "0",
            "device_class": "problem"
        }
        config["device"] = SYSTEM_DEVICE_INFO
        client.publish(f"homeassistant/binary_sensor/zity_alarm_{name}/config", json.dumps(config), retain=True)

# ------------------ Manual Override Functions ------------------ #

def check_manual_override(zone_id, current_values):
    """Check if manual override should be activated for a zone"""
    global manual_override_states, last_mqtt_values

    check_needed = True

    # Aquire lock to make sure values are not being updated.
    with state_lock:
        if manual_override_states[zone_id]:
            # Already in override mode, don't check
            check_needed = False
        last_values = last_mqtt_values[zone_id]

    # If manual override is already active for the zone, don't check.
    if not check_needed:
        logger.info(f"Zone {zone_id}: Skipped; manual override already activated")
        return

    # Check if any value has changed from what we last set via MQTT
    changes_detected = False

    # Check setpoint (temperature)
    if (last_values['temp'] is not None and
        current_values.get('setpoint') is not None and
        float(current_values['setpoint']) != float(last_values['temp'])):
        logger.info(f"Zone {zone_id}: Manual setpoint change detected: {last_values['temp']} -> {current_values['setpoint']}")
        changes_detected = True

    # Check mode
    if (last_values['mode'] is not None and
        current_values.get('mode') is not None and
        current_values['mode'] != last_values['mode']):
        logger.info(f"Zone {zone_id}: Manual mode change detected: {last_values['mode']} -> {current_values['mode']}")
        changes_detected = True

    # Check fan mode
    if (last_values['fan_mode'] is not None and
        current_values.get('fan_mode') is not None and
        current_values['fan_mode'] != last_values['fan_mode']):
        logger.info(f"Zone {zone_id}: Manual fan mode change detected: {last_values['fan_mode']} -> {current_values['fan_mode']}")
        changes_detected = True

    # Check preset mode
    if (last_values['preset_mode'] is not None and
        current_values.get('preset_mode') is not None and
        current_values['preset_mode'] != last_values['preset_mode']):
        logger.info(f"Zone {zone_id}: Manual preset mode change detected: {last_values['preset_mode']} -> {current_values['preset_mode']}")
        changes_detected = True

    if changes_detected:
        set_manual_override(zone_id, True)
    else:
        logger.info(f"Zone {zone_id}: No manual changes detected")

def set_manual_override(zone_id, state):
    """Set manual override state for a zone"""

    global manual_override_states, last_mqtt_values

    # Acquire lock to make sure values are not being used anywhere else while we're updating.
    with state_lock:
        last_mqtt_values[zone_id]['reset'] = not(state)
        manual_override_states[zone_id] = state

    payload = "ON" if state else "OFF"
    client.publish(f"{base_topic}/zone/{zone_id}/manual_override", payload, retain=True)
    logger.info(f"Zone {zone_id}: Manual override set to {payload}")

def load_retained_manual_override_states():
    """Load retained manual override states from MQTT broker"""
    def on_manual_override_message(client, userdata, msg):
        try:
            topic_parts = msg.topic.split('/')
            zone_id = topic_parts[-2]  # Get zone_id from topic
            if zone_id in zones:
                payload = msg.payload.decode().upper()
                manual_override_states[zone_id] = (payload == "ON")
                logger.info(f"Zone {zone_id}: Loaded retained manual override state: {payload}")
        except Exception as e:
            logger.error(f"Error loading retained manual override state: {e}")

    # Subscribe to all manual override topics to get retained messages
    temp_client = mqtt.Client()
    temp_client.username_pw_set(config["mqtt"]["username"], config["mqtt"]["password"])
    temp_client.on_message = on_manual_override_message

    try:
        temp_client.connect(config["mqtt"]["broker"], config["mqtt"]["port"], 60)
        for zone_id in zones:
            temp_client.subscribe(f"{base_topic}/zone/{zone_id}/manual_override")

        # Wait briefly for retained messages
        temp_client.loop_start()
        time.sleep(2)
        temp_client.loop_stop()
        temp_client.disconnect()
    except Exception as e:
        logger.error(f"Error loading retained manual override states: {e}")

# ------------------ MQTT Event Handlers ------------------ #

def on_connect(client, userdata, flags, rc):
    logger.info("Connected to MQTT broker.")

    # Load retained manual override states first
    load_retained_manual_override_states()

    for zone_id in zones:
        client.subscribe(f"{base_topic}/zone/{zone_id}/set_temp")
        client.subscribe(f"{base_topic}/zone/{zone_id}/set_mode")
        client.subscribe(f"{base_topic}/zone/{zone_id}/set_fan_mode")
        client.subscribe(f"{base_topic}/zone/{zone_id}/set_preset_mode")
        client.subscribe(f"{base_topic}/zone/{zone_id}/set_manual_override")
        publish_discovery(zone_id)

        # Publish initial manual override state
        payload = "ON" if manual_override_states[zone_id] else "OFF"
        client.publish(f"{base_topic}/zone/{zone_id}/manual_override", payload, retain=True)

    client.subscribe(f"{base_topic}/system/set_mode")
    client.subscribe(f"{base_topic}/system/set_power")
    publish_system_discovery()

def on_message(client, userdata, msg):
    global last_mqtt_values
    topic = msg.topic
    payload = msg.payload.decode()

    if topic == f"{base_topic}/system/set_mode":
        try:
            mb.write_registers(trigger_register, [1], slave=slave_id)
            time.sleep(0.2)
            if payload in state_list:
                idx = state_list.index(payload)
                # Changing registers and last_mqtt_values, so acquire lock
                with state_lock:
                    mb.write_registers(system_mode_write_register, [idx], slave=slave_id)
                    client.publish(f"{base_topic}/system/mode", payload, retain=True)
                    logger.info(f"System mode set to {payload}")
                    # Update last_mqtt_values for all zones, but only if the zone is not "off"
                    for zid in zones:
                        if last_mqtt_values[zid]['mode'] != "off":
                            last_mqtt_values[zid]['mode'] = payload
                            last_mqtt_values[zid]['postpone'] = latency
                            client.publish(f"{base_topic}/zone/{zid}/mode", payload, retain=True)
                            logger.info(f"Zone {zid}: mode set to {payload}")
                        else:
                            logger.info(f"Zone {zid}: zone is switched off; remains off")
        except Exception as e:
            logger.error(f"Error setting system mode: {e}")
        return
    elif topic == f"{base_topic}/system/set_power":
        try:
            mb.write_registers(trigger_register, [1], slave=slave_id)
            time.sleep(0.2)
            value = 0 if payload == "off" else 1
            mb.write_registers(system_power_mode_write_register, [value], slave=slave_id)
            client.publish(f"{base_topic}/system/power_mode", payload, retain=True)
            logger.info(f"System power mode set to {payload}")
        except Exception as e:
            logger.error(f"Error setting system power mode: {e}")
        return

    zone_id = topic.split("/")[-2]
    zone = zones.get(zone_id)
    if not zone:
        return

    # Handle manual override set commands. This does not require setting any registers
    # so we handle this outside the try block.
    if topic.endswith("/set_manual_override"):
        state = payload.upper() == "ON"
        set_manual_override(zone_id, state)
        return

    try:
        mb.write_registers(trigger_register, [1], slave=slave_id)
        time.sleep(0.2)

        if topic.endswith("/set_temp"):
            value = int(float(payload) * 10)
            with state_lock:
                mb.write_registers(zone["setpoint_write_register"], [value], slave=slave_id)
                # Store the MQTT value.
                last_mqtt_values[zone_id]['temp'] = float(payload)
                last_mqtt_values[zone_id]['postpone'] = latency
            client.publish(f"{base_topic}/zone/{zone_id}/setpoint", payload, retain=True)
            logger.info(f"Zone {zone_id}: setpoint set to {payload}")

        elif topic.endswith("/set_mode"):
            value = 0 if payload.lower() == "off" else 1
            overall_status_result = mb.read_input_registers(overall_status_register, 1, slave=slave_id)
            if payload.lower() != "off":
                payload = state_list[overall_status_result.registers[0]]
            with state_lock:
                mb.write_registers(zone["status_write_register"], [value], slave=slave_id)
                # Store the MQTT value
                last_mqtt_values[zone_id]['mode'] = payload
                last_mqtt_values[zone_id]['postpone'] = latency
            client.publish(f"{base_topic}/zone/{zone_id}/mode", payload, retain=True)
            logger.info(f"Zone {zone_id}: mode set to {payload}")
        elif topic.endswith("/set_fan_mode"):
            value = fan_mode_list.index(payload.lower())
            for zid, zconf in zones.items():
                mb.write_registers(zconf["master_slave_register"], [1 if zid == master_zone else 0], slave=slave_id)
                time.sleep(0.1)
                mb.write_registers(trigger_register, [1], slave=slave_id)
                time.sleep(0.1)
                mb.write_registers(zconf["fan_control_register"], [1], slave=slave_id)
                time.sleep(0.1)
                mb.write_registers(trigger_register, [1], slave=slave_id)
            with state_lock:
                mb.write_registers(zone["fan_mode_write_register"], [value], slave=slave_id)
                # Store the MQTT value
                last_mqtt_values[zone_id]['fan_mode'] = payload.lower()
                last_mqtt_values[zone_id]['postpone'] = latency
            client.publish(f"{base_topic}/zone/{zone_id}/fan_mode", payload.lower(), retain=True)
            logger.info(f"Zone {zone_id}: fan mode set to {payload}")

        elif topic.endswith("/set_preset_mode"):
            value = 0 if payload.lower() == "none" else 1
            with state_lock:
                mb.write_registers(zone["preset_mode_write_register"], [value], slave=slave_id)
                # Store the MQTT value
                last_mqtt_values[zone_id]['preset_mode'] = payload
                last_mqtt_values[zone_id]['postpone'] = latency
            client.publish(f"{base_topic}/zone/{zone_id}/preset_mode", payload, retain=True)
            logger.info(f"Zone {zone_id}: preset mode set to {payload}")

    except Exception as e:
        logger.error(f"MQTT message error: {e}")

# ------------------ Polling ------------------ #

def poll_zone_status():
    global first_poll_completed
    time.sleep(10)
    while True:
        if not mb.connected:
            logger.info("Modbus disconnected. Trying to reconnect...")
            try:
                mb.connect()
            except Exception as e:
                logger.error(f"Reconnection failed: {e}")
                time.sleep(5)
                continue
        try:
            with state_lock:
                mode_val = mb.read_input_registers(system_registers["mode"], 1, slave=slave_id).registers[0]
        except Exception as e:
            logger.error(f"Error reading system mode: {e}")
            time.sleep(5)
            continue

        for zone_id, zone in zones.items():
            try:
                with state_lock:
                    do_override_check = (last_mqtt_values[zone_id]['postpone'] == 0)
                    temp = mb.read_input_registers(zone["temp_read_register"], 1, slave=slave_id).registers[0] / 10.0
                    setpoint = mb.read_input_registers(zone["setpoint_read_register"], 1, slave=slave_id).registers[0] / 10.0
                    damper = mb.read_input_registers(zone["damper_status_read_register"], 1, slave=slave_id).registers[0]
                    power = mb.read_input_registers(zone["status_read_register"], 1, slave=slave_id).registers[0]
                    fan_mode = mb.read_input_registers(zone["fan_mode_read_register"], 1, slave=slave_id).registers[0]
                    preset_mode = mb.read_input_registers(zone["preset_mode_read_register"], 1, slave=slave_id).registers[0]

                    mode = "off" if power == 0 else state_list[mode_val]
                    fan_mode_str = fan_mode_list[fan_mode]
                    preset_mode_str = "eco" if preset_mode else "none"

                    # Prepare current values for manual override check
                    current_values = {
                        'setpoint': setpoint,
                        'mode': mode,
                        'fan_mode': fan_mode_str,
                        'preset_mode': preset_mode_str
                    }

                    # On first poll, initialize last_mqtt_values with current values
                    # This prevents false positives after restart.
                    # Also do this if the manual override switch was reset to "off". In this case, we must
                    # act as if the current values are the last ones sent through MQTT.
                    if (not first_poll_completed  and temp > 10 and temp < 50 and setpoint > 10 and setpoint < 50) or (last_mqtt_values[zone_id]['reset']):
                        last_mqtt_values[zone_id]['temp'] = setpoint
                        last_mqtt_values[zone_id]['mode'] = mode
                        last_mqtt_values[zone_id]['fan_mode'] = fan_mode_str
                        last_mqtt_values[zone_id]['preset_mode'] = preset_mode_str
                        last_mqtt_values[zone_id]['reset'] = False

                # Check for manual override (only if values are valid, not first poll and not right after the zone was updated through MQTT)
                if first_poll_completed and temp > 10 and temp < 50 and setpoint > 10 and setpoint < 50 and do_override_check:
                    check_manual_override(zone_id, current_values)
                elif not first_poll_completed:
                    logger.info(f"Zone {zone_id}: Waiting for first poll to complete.")
                elif do_override_check:
                    logger.info(f"Zone {zone_id}: Invalid temperature or setpoint values found; skipping override check.")
                else:
                    with state_lock:
                        waits = last_mqtt_values[zone_id]['postpone']
                        logger.info(f"Zone {zone_id}: Manual override check postponed. Waits: {waits}.")
                        last_mqtt_values[zone_id]['postpone'] -= 1

                # Publish the values we cannot change through MQTT.

                # Sometimes, right after (re-) starting the Zity, it comes up with incorrect values. Don't publish these.
                if temp > 10 and temp < 50:
                    client.publish(f"{base_topic}/zone/{zone_id}/temp", temp, retain=True)
                client.publish(f"{base_topic}/zone/{zone_id}/damper_status", "open" if damper else "closed", retain=True)

                # Only publish the values that can be changed if there were no recent MQTT changes. This gives the Zity
                # some time to propagate the settings from the write to the read registers. Otherwise, this might publish
                # one or more older values.

                if do_override_check:

                    logger.info(f"Zone {zone_id}: Publishing values.")
                    if setpoint > 10 and setpoint < 50:
                        client.publish(f"{base_topic}/zone/{zone_id}/setpoint", setpoint, retain=True)
                    client.publish(f"{base_topic}/zone/{zone_id}/power", "on" if power else "off", retain=True)
                    client.publish(f"{base_topic}/zone/{zone_id}/mode", mode, retain=True)
                    client.publish(f"{base_topic}/zone/{zone_id}/fan_mode", fan_mode_str, retain=True)
                    client.publish(f"{base_topic}/zone/{zone_id}/preset_mode", preset_mode_str, retain=True)
                else:
                    logger.info(f"Zone {zone_id}: Postponing MQTT messages. Values in dict: {current_values}.")

                logger.debug(f"Zone {zone_id} status: setpoint '{setpoint}', damper '{damper}', power '{power}', mode '{mode}', fan_mode '{fan_mode}', fan_mode_str '{fan_mode_str}', preset_mode '{preset_mode}', preset_mode_str '{preset_mode_str}'")


            except Exception as e:
                logger.error(f"Polling error in zone {zone_id}: {e}")

        # Mark first poll as completed after processing all zones
        if not first_poll_completed:
            first_poll_completed = True
            logger.info("First poll completed - manual override detection now active")

        # System-level registers
        for key, reg in system_registers.items():
            if "write" in key:
                continue
            try:
                val = mb.read_input_registers(reg, 1, slave=slave_id).registers[0]
                if "temp" in key:
                    val = val / 10.0
                    # Prevent weird values (could occur just after startup).
                    # Just assume it's 21 in that case.
                    if val < 10 or val > 50:
                        val = 21
                elif key == "setpoint":
                    if val < 10 or val > 50:
                        val = 21
                elif key == "mode":
                    index = val
                    val = state_list[0]
                    val = state_list[index]
                elif key == "power_mode" or key == "controller_mode":
                    val = "on" if val == 1 else "off"
                elif "flexi" in key:
                    index = val
                    val = fan_mode_list[0]
                    val = fan_mode_list[index]
                elif key == "fan_speed":
                    index = val
                    val = system_fan_mode_list[0]
                    val = system_fan_mode_list[index]
                client.publish(f"{base_topic}/system/{key}", val, retain=True)
                logger.debug(f"System-level register {key}: {val}")
            except Exception as e:
                logger.error(f"System read error {key}: {e}")

        # Alarms
        for reg, name in alarm_registers.items():
            try:
                val = mb.read_input_registers(reg, 1, slave=slave_id).registers[0]
                client.publish(f"{base_topic}/system/alarm_{reg}", str(val), retain=True)
            except Exception as e:
                logger.error(f"Alarm read error {reg}: {e}")

        time.sleep(30)

# ------------------ Start ------------------ #
client = mqtt.Client()
client.username_pw_set(config["mqtt"]["username"], config["mqtt"]["password"])
client.on_connect = on_connect
client.on_message = on_message
client.connect(config["mqtt"]["broker"], config["mqtt"]["port"], 60)

mb.connect()
threading.Thread(target=poll_zone_status, daemon=True).start()

while True:
    try:
        client.loop_forever()
    except Exception as e:
        logger.error(f"MQTT connection lost: {e}. Reconnecting in 5s...")
        time.sleep(5)
        
