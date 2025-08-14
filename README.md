# zity-mqtt-bridge
A Python bridge to allow MQTT communications with the Zoning Zity 2.0 controller. The Zity controls air conditioning zones and communicates through Modbus. The Bridge was designed to integrate directly with Home Assistant.

# Background
Our airconditiong system is a central system, with pipes running to several zones in our house. Each zone has its own thermostat and there are dampers in the pipes allowing airflow to a room to be opened or closed. The [Zoning Zity-RC 2.0 control unit](https://zoning.es/en/archivos/productos/zoning-system-zity-rc-2-0) takes care of opening and closing the valves. It also controls the airconditioning unit. My goal was to connect the Zity to Home Assistant, so I could create Climate entities for the zones, which would give me virtual thermostats in Home Assistant and the ability to use automations to change setpoints and other things. The company responsible for the installation of the system kindly provided the specifications for the RS485 Modbus interface. The documentation was in Spanish, but with an automated translation and some general knowledge of the Modbus protocol, it was clear enough for me to work with. With a Raspberry Pi, an RS485 to USB converter and Home Assistant, I should have everything I need.

# Home Assistant Modbus
My initial idea was to use Home Assistant's Modbus integration. I had never set up such a thing, so I turned to AI to get some help. Sure enough, I managed to get a configuration file that Home Assistant could read. However, I soon realized that changing settings on the Zity through Modbus would not be possible with the standard Modbus integration. This is because the Zity requires you to first write one Modbus register to indicate you are taking control, then write the actual register you want to change. I was afraid my adventure would end right there and I would need the [Netbox](https://zoning.es/en/archivos/productos/netbox-interface-de-comunication-cloud) cloud communication interface. After venting my frustration about this to ChatGPT, it came up with a workaround that I had not thought of: a bridge that would allow me to communicate with the Zity in the way described in the documentation, while providing an MQTT interface towards Home Assistant.

# The Zity MQTT Bridge
Sure enough, with lots of help from ChatGPT and later Claude AI as well, I developed a Python program that does just that. In the process, it has been extended with MQTT discover messages so it will automatically configure the entities it provides. This is what it provides for every zone in your Zity configuration:

* A fully functional Climate entity, which will read:
  * the setpoint
  * the mode (off, cool, heat, dry, fan_only)
  * the fan speed
  * the preset mode (normal or eco)
  * the current temperature in the zone
* Besides reading, the Climate entity can also be used to change:
  * the setpoint
  * the fan speed
  * the preset mode
* Sensors for the following:
  * Damper status (open or closed)
  * The current temperature (this is also an attribute of the climate entity, but sometimes a separate sensor is useful to have)
  * The setpoint (also an attribute of the climate entity)
* Manual override switch. This will switch to "ON" when the physical thermostat in the zone has been used to change a setting. This can be useful in automations. I'm using it to obey to the rule that everything in our household can be automated (by me), as long as the physical buttons, switches etc, can always be used to override any automation. The switch can also be set from Home Assistant. By switching them off somewhere after midnight, I'm allowing the automations to take over again at the beginning of a new day.

Besides settings per zone, there are also system-level entities provided by the Zity MQTT Bridge:

* An overall power switch to switch off the entire system, effectively switching all the zones to "OFF" at once.
* A select entity that sets the mode of the system (off, coo, heat, dry or fan_only). This is a system-level setting, because there is only one airconditioning unit. This means that all the zones are always in the same mode. Whenever you set this at the system level, the Bridge automatically sends out status updates to all the zone climate entities, so they will change immediately as well. The other way around: if you are trying to change the mode in a single zone, the Bridge will immediately send an update that changes the mode back to the system-level mode.
* Binary sensors for alarms.
* Sensors for the following:
  * System setpoint. This is the temperature setting that the Zity controller sets the central airconditioning unit to. It depends on the setpoint settings per zone and the damper status per zone. This is calculated by Zity and cannot be changed.
  * The return temperature. This is the temperature of the air that's being fed back into the central airconditioning unit.
  * The status (on or off). This basically indicates if there are any zones that have their dampers open.
  * The fan speed
  * The FlexiFan setting. FlexiFan is the way Zity translates the zone fan speed settings into a single setting, effecting the fan speed.

# Configuration file
The file zity_config.yaml can be used to configure the following:
* The zones you have. It should not be needed to change the register addresses in there, but I would recommend changing the name because that becomes visible in Home Assistant.
* MQTT communication settings. Change this for your situation.
* Modbus settings. The only thing you might want to change there, is the port.
* The trigger register. Don't change.
* The master zone. Change this to reflect which zone (thermostat) has been configured as the master.
* System registers. Don't change.
* Alarm registers. No need to change, unless you want less (or more) alarm types. The one named "heavybox" (register 2087) can be named differently, depending on the actual interface you are using. An interface in this context is the physical connection between the Zity controller and the airconditioning unit. In my case, I'm using a Mitsubishi Heavy Industries unit and that requires the Heavybox interface. See the [interfaces page](https://zoning.es/en/inicio/tecnico/productos) on the Zoning website. In any case, it's just a name and you could also name it "interface" to make it more generic.
* Loglevel. This is the level of logging for the Python script. Setting it to ERROR is the recommended setting when you're running this as a service on a Raspberry Pi, so it won't generate a lot of logging. Set it to anthing lower (INFO or DEBUG) to see more of what's happening. DEBUG wil also switch on debugging for the libraries the Bridge is using.

# Prerequisites
As this is a Python script, you need to have Python installed. It also needs libraries for YAML, MQTT and Modbus, so install pymodbus, paho-mqtt and pyyaml.
