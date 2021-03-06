import machine
import time
import socket
import ure as re
#import micropython

#micropython.alloc_emergency_exception_buf(100)

CONFIG = {
    "broker": "192.168.1.19", # overwritten by contents of file "/broker.ip"
    "udp_port_gpio": 8266, # of IP address above, for UDP datagrams on input GPIO transitions
    "udp_port_adc": 8267, # of IP address above, for UDP datagrams on ADC readings
    "client_id": b"esp8266_bedroom",
    "topic": b"home",
    "input_gpio": [
        {
            "pin": 4,
            "name": "Switch #2",
            "on_bytes":  b'\x02\xff',
            "off_bytes": b'\x02\x00',
        },
        {
            "pin": 2,
            "name": "Switch #3",
            "on_bytes":  b'\x03\xff',
            "off_bytes": b'\x03\x00',
        },
        {
            "pin": 5,
            "name": "Switch #4",
            "on_bytes":  b'\x04\xff',
            "off_bytes": b'\x04\x00',
        },
    ],
    "output_gpio": [
        {
            "pin": 14,
            "name": "AC Outlet",
            "on_path": "/outlet/on",
            "off_path": "/outlet/off",
        },
        {
            "pin": 16,
            "name": "Internal Green LED",
            "on_path": "/led/on",
            "off_path": "/led/off",
        },
        {
            "pin": 15,
            "name": "Buzzer",
            "on_path": "/buzzer/on",
            "off_path": "/buzzer/off",
            "pwm": True,
        },
    ],
    "interval": 0.01, # 10 ms
    "adc_count_interval": 100, # every N*interval, poll the ADC
    "adc_min_delta": 5, # only report changes if ADC differs by this amount from last poll interval
}

client = None

def load_config():
    try:
        with open("/broker.ip") as f:
            # All configuration is specified above except this one field can be overridden
            CONFIG['broker'] = f.read().strip()
    except (OSError, ValueError):
        print("Couldn't load /broker.ip, assuming default broker {}".format(CONFIG['broker']))
    print("Loaded config from /broker.ip")


def serve_web_client(cl, addr, CONFIG, analog_value, name2value):
    print('Accepted connection:',cl,addr)
    cl.settimeout(1) # larger timeout than accept() timeout
    cl_file = cl.makefile('rwb', 0)
    try:
        request_line = ""
        while True:
            line = cl_file.readline()
            #print('Request line: {}'.format(line))
            if not line or line == b'\r\n':
                break
            # hack: check for matching URL strings anywhere in what looks like the HTTP request line TODO: properly parse
            # also, allowing any of these verbs for convenience, though technically should PUT only change state? TODO restfulness
            if line.startswith('GET ') or line.startswith('POST ') or line.startswith('PUT '):
                request_line = line

        for info in CONFIG['output_gpio']:
            new_value = None
            if info['on_path'] in request_line:
                new_value = True
            if info['off_path'] in request_line:
                new_value = False

            if new_value is not None:
                print('Request via {} to change value of GPIO output: {} ({}) -> {}'.format(line, info['name'], info['pin'], new_value))
                if info.get('pwm'):
                    # pulse-width modulation
                    if 'object' not in info:
                        obj = machine.PWM(machine.Pin(info['pin']))
                        info['object'] = obj # cached to avoid recreating objects
                    obj = info['object']
                    if new_value is False:
                        obj.duty(0)
                        # TODO: could deinit(), but this works alright
                    else:
                        # on, but at what frequency / duty cycle?
                        match = re.search(r'freq=(\d+)&duty=(\d+)', request_line)
                        if match:
                            freq = int(match.group(1))
                            duty = int(match.group(2))
                        else:
                            freq = 10
                            duty = 512

                        # TODO: why doesn't this regex-free code load on boot?
                        #sline = str(request_line)
                        #at = sline.find('freq=')
                        #freq = None
                        #duty = None
                        #if at != -1:
                        #    s = sline[at + 5:]
                        #    freq = int(s[:s.rfind('&')])
                        #at = sline.find('duty=')
                        #if at != -1:
                        #    s = sline[at + 5:]
                        #    duty = int(s[:s.rfind(' ')])
               
                        if freq is None: freq = 10
                        if duty is None: duty = 512
                        obj.freq(freq)
                        obj.duty(duty)
 
                else:
                    # regular GPIO on/off
                    if 'object' not in info:
                        obj = machine.Pin(info['pin'], machine.Pin.OUT)
                        info['object'] = obj # cached to avoid recreating objects
                    obj = info['object']
                    obj.value(new_value)

                # TODO: redirect back to homepage


    except Exception as e:
        print('Exception from client:',e)
        pass

    title = CONFIG['client_id'].decode('ascii')
    html = """<html>
<head>
<title>{}</title>
</head>
<body>
<h1>{}</h1>
<table>
 <tr>
  <td>Analog Sensor</td>
  <td>{}</td>
 </tr>
""".format(title, title, analog_value)

    for name in sorted(name2value.keys()):
        value = name2value[name]
        html += """
 <tr>
  <td>{}</td>
  <td>{}</td>
 </tr>""".format(name, value)
 
    for info in CONFIG['output_gpio']:
        html += """
 <tr>
  <td>{}</td>
  <td><a href="{}">On</a> | <a href="{}">Off</a></td>
 </tr>
""".format(info['name'], info['on_path'], info['off_path'])

    html += """
</table>
</body>
</html>"""

    response = """HTTP/1.1 200 OK\r
Content-Type: text/html\r
Content-Length: {}\r
\r
{}
""".format(len(html), html)

    cl.send(response)
    cl.close()

def notify_gpio(CONFIG, name2config, name, value):
    info = name2config[name]
   
    if not value:
        data = info['on_bytes'] # active-low!
    else:
        data = info['off_bytes']

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    address = (CONFIG['broker'], CONFIG['udp_port_gpio'])
    s.sendto(data, address)
    print('Sent datagram to {}: {}'.format(address, data))
    # connectionless

def notify_adc(CONFIG, value):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    address = (CONFIG['broker'], CONFIG['udp_port_adc'])
    data = str(value)
    s.sendto(data, address)
    print('Sent datagram to {}: {}'.format(address, data))
    # connectionless



global any_gpio_changed
any_gpio_changed = False

def main():
    global any_gpio_changed
    load_config()
    analog_pin = machine.ADC(0) # pin A0 on ESP8266
    #client = MQTTClient(CONFIG['client_id'], CONFIG['broker']) # TODO: what could cause "ImportError: cannot import name MQTTClient"?
    #client.connect()
    #print("Connected to {}".format(CONFIG['broker']))

    # interrupt-driven switches
    print('Switches:',CONFIG['input_gpio'])
    name2pin = {}
    name2old_value = {}
    name2config = {}
    for info in CONFIG['input_gpio']:
        pin_number = info['pin']
        name = info['name']

        pin = machine.Pin(pin_number, machine.Pin.IN, machine.Pin.PULL_UP)
        name2pin[name] = pin
        name2old_value[name] = 1 # active-low; assume starts off inactive
        name2config[name] = info

        def add_handler(pin_number, name):
            def handler(ignored): # ISR argument is a Pin object, which can't convert back to a pin number?! Ignore it and use closure
                global any_gpio_changed
                any_gpio_changed = True
                # important: can't do much in an ISR, see https://micropython.org/resources/docs/en/latest/wipy/reference/isr_rules.html
                #print('read {}: {}'.format(pin,pin.value()))
            print('Watching pin {} = {}'.format(pin_number, name))
            pin.irq(trigger=machine.Pin.IRQ_RISING | machine.Pin.IRQ_FALLING, handler=handler)

        add_handler(pin_number, name)

    # serve
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.settimeout(CONFIG['interval']) # seconds to wait
    s.bind(addr)
    s.listen(1)
    print('Web server listening on',addr)

    # poll
    i = None
    analog_value = -1
    old_analog_value = analog_value
    #watchdog = machine.WDT(timeout=5000) # reboot if not fed within 5 seconds - TypeError: function doesn't take keyword arguments
    #watchdog = machine.WDT(5000) # is this five seconds?
    watchdog = machine.WDT() # is this five seconds?
    while True:
        watchdog.feed()

        if i is None or i == CONFIG['adc_count_interval']:
            analog_value = analog_pin.read()
            #TODO
            #client.publish('{}/{}'.format(CONFIG['topic'],
            #                                  CONFIG['client_id']),
            #                                  bytes(str(analog_value), 'utf-8'))
            delta = abs(analog_value - old_analog_value)
            if delta >= CONFIG['adc_min_delta']:
                print('Sensor state: {} -> {}'.format(old_analog_value, analog_value))
                notify_adc(CONFIG, analog_value)

            i = 0
            old_analog_value = analog_value

        i += 1

        if any_gpio_changed:
            print('Saw some GPIO changes')
            any_gpio_changed = False

            # Get new values of all pins
            for name, pin in name2pin.items():
                old_value = name2old_value[name]
                value = pin.value()
                if old_value != value:
                    print('GPIO pin {}: {} -> {}'.format(name, old_value, value))
                    notify_gpio(CONFIG, name2config, name, value)

                # Save old value for next time
                name2old_value[name] = value

        try:
            cl, addr = s.accept()
        except:
            pass
        else:
            serve_web_client(cl, addr, CONFIG, analog_value, name2old_value)

        #time.sleep(CONFIG['interval'])

if __name__ == '__main__':
    main()
