#!/usr/bin/python
# -*- coding:utf-8 -*-

import os, sys, configparser, random, time, logging, io, math, sqlite3, threading, datetime
from enum import Enum
from waveshare_epd import epd2in13_V3
from PIL import Image, ImageDraw, ImageFont
from paho.mqtt import client as mqtt_client
from queue import Queue

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

def resolve_relative_path(path):
    if path.startswith("/"):
        return path
    else:
        return SCRIPT_DIR + "/" + path

config = configparser.ConfigParser()
config.read(SCRIPT_DIR + "/config.ini")

broker = config.get("mqtt", "broker_address")
port = int(config.get("mqtt", "broker_port"))
topic = config.get("mqtt", "topic")
client_id = config.get("mqtt", "client_id_prefix") + f'-{random.randint(0, 1000)}'
username = config.get("mqtt", "username")
password = config.get("mqtt", "password")
database_file = resolve_relative_path(config.get("db", "path"))
cur_qr_path = resolve_relative_path(config.get("qrcode", "path"))

tls_ca_path = resolve_relative_path(config.get("mqtt", "tls_ca_path"))

FIRST_RECONNECT_DELAY = config.get("mqtt", "first_connect_delay")
RECONNECT_RATE = int(config.get("mqtt", "reconnect_rate"))
MAX_RECONNECT_DELAY = config.get("mqtt", "max_reconnect_delay")

class SystemState(Enum):
    STARTUP = 0
    QR_CODE = 1
    DRAWING = 2
    BLANKED = 3
    SHUTDOWN = 4

system_state = SystemState.STARTUP

def display_text(text):
    image = Image.new('1', (epd.height, epd.width), 255)
    draw = ImageDraw.Draw(image)
    draw.text((0, 0), text, font=hack16, fill=0)
    display_upside_down(image)

def display_upside_down(image):
    image = image.rotate(180)
    epd.display(epd.getbuffer(image))

def scale_letterboxed(image, new_width, new_height):
    # first compute the scale factor based on the height
    scaleFactor = new_height / image.height

    # if the height-based scale factor would result in a width that is still too
    # large, update the scale factor to be based on the width
    if math.trunc(image.width * scaleFactor) > new_width:
        scaleFactor = new_width / image.width

    image = image.resize((
        math.trunc(image.width * scaleFactor),
        math.trunc(image.height * scaleFactor)
    ))

    # if we ended up with an image the exact desired dimensions, just return it.
    # otherwise, we need to paste the scaled image into the center of a new
    # blank image
    if image.width == new_width and image.height == new_height:
        return image

    newImage = Image.new('1', (new_width, new_height), 255)

    newImage.paste(image, (
        math.trunc((new_width / 2) - (image.width / 2)),
        math.trunc((new_height / 2) - (image.height / 2))
    ))

    return newImage

# scale, rotate, and put put the image on the screen
def display_image_full(image):
    if image.height != 122 or image.width != 250:
            image = scale_letterboxed(image, 250, 122)
    epd.init()
    display_upside_down(image)
    epd.sleep()

def display_image_from_bytes(bytes):
    display_image_full(Image.open(io.BytesIO(bytes)))

def next_drawing_available(cur):
    res = cur.execute("SELECT COUNT(*) FROM `drawings` WHERE displayed_time IS NULL AND `removed` = 0;")
    count = res.fetchone()[0]
    return int(count) > 0

def display_qr_from_disk():
    try:
        image = Image.open(cur_qr_path)
    except:
        logging.error("Unable to open QR code image. Something wrong with state.")
        return
    display_image_full(image)

def display_next_drawing(cur, con):
    global last_drawing_displayed_id
    res = cur.execute("SELECT * FROM `drawings` WHERE `displayed_time` IS NULL AND `removed` = 0 ORDER BY `created_time` ASC LIMIT 1;")
    row = res.fetchone()
    if row == None:
        logging.error("Couldn't display next drawing because none are available. Something's wrong with the state.")
        return
    display_image_full(Image.open(io.BytesIO(row[4])))
    last_drawing_displayed_id = row[0]
    
    # mark the selected drawing as having been displayed
    cur.execute(
        "UPDATE `drawings` SET `displayed_time` = datetime('now') WHERE `id` = ?",
        (str(row[0]), )
    )
    con.commit()

def parse_sqlite_date(date_str):
    return datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

def image_timer_loop(precision):
    global system_state, database_file, last_drawing_displayed_id

    # sqlite3 connection from parent thread cannot be used so create one just for this function
    con = sqlite3.connect(database_file)
    cur = con.cursor()

    while True:
        if system_state == SystemState.DRAWING:
            # get the most recently displayed image
            res = cur.execute("SELECT * FROM `drawings` WHERE `displayed_time` IS NOT NULL ORDER BY `displayed_time` DESC LIMIT 1")
            row = cur.fetchone()

            # compute when the currently displayed image should be replaced
            if row == None:
                # no last-displayed image was known, hard-set expiration date in the past
                expiration_time = datetime.datetime.utcfromtimestamp(0)
            else:
                last_displayed_drawing_displayed_time = parse_sqlite_date(row[2])
                #expiration_time = last_displayed_drawing_displayed_time + datetime.timedelta(minutes = 5)
                expiration_time = last_displayed_drawing_displayed_time + datetime.timedelta(seconds = 30)
            
            # replace the image if it is time
            if expiration_time < datetime.datetime.utcnow():
                if next_drawing_available(cur):
                    display_next_drawing(cur, con)
                else:
                    display_qr_from_disk()
                    
        elif system_state == SystemState.SHUTDOWN:
            break

        time.sleep(precision)

def mqtt_connect():
    def on_connect(client, userdata, flags, rc):
        # display_text("MQTT connection started")
        def on_subscribe(client, userdata, message_id, granted_qos):
            print('Subscription granted for message_id ' + str(message_id) + ', QOS ' + str(granted_qos[0]))
            # display_text("Subscription granted\nMQTT ready\nmessage_id: " + str(message_id) + "\nQOS: " + str(granted_qos[0]))

        # Main handler for incoming messages - this is the where it all happens!
        def on_message(client, userdata, message):
            global system_state, cur, con, last_drawing_displayed_id
            if message.topic == "epaper/cmnd/update-qr":
                # Overwrite the current QR code on disk
                f = open(cur_qr_path, 'wb')
                f.write(message.payload)
                f.close()
                logging.info("Updated locally stored QR code")

                # If we are currently showing the QR code or have just started
                # up, update the screen now
                if system_state == SystemState.QR_CODE:
                    display_image_from_bytes(message.payload)
                elif system_state == SystemState.STARTUP:
                    system_state = SystemState.QR_CODE
                    display_image_from_bytes(message.payload)
            elif message.topic.startswith("epaper/cmnd/image/add/"):
                # save the image locally
                drawing_id = int(message.topic[message.topic.rfind("/") + 1:])
                if system_state == SystemState.QR_CODE:
                    # if we're waiting for a drawing, show it immediately and then save it already marked as displaye
                    system_state = SystemState.DRAWING
                    last_drawing_displayed_id = drawing_id
                    display_image_from_bytes(message.payload)
                    cur.execute(
                        "INSERT INTO `drawings` (id, created_time, displayed_time, removed, data) VALUES (?, datetime('now'), datetime('now'), 0, ?)",
                        (drawing_id, message.payload)
                    )
                else:
                    # just save it
                    cur.execute(
                        "INSERT INTO `drawings` (id, created_time, removed, data) VALUES (?, datetime('now'), 0, ?)",
                        (drawing_id, message.payload)
                    )
                con.commit()
            elif message.topic == "epaper/cmnd/image/remove":
                try:
                    id = int(message.payload)
                    print("remove image " + str(id))
                except ValueError:
                    print("Received id to remove didn't parse as an int")
                    return
                cur.execute("UPDATE `drawings` SET `removed`=1 WHERE `id`=?", (id, ))
                con.commit()
                if last_drawing_displayed_id == id:
                    if next_drawing_available(cur):
                        display_next_drawing(cur, con)
                    else:
                        system_state = SystemState.QR_CODE
                        display_qr_from_disk()
            elif message.topic == "epaper/cmnd/blank":
                payload_str = message.payload.decode("utf-8")
                if payload_str == "true":
                    # Immediately enter blanked state
                    system_state = SystemState.BLANKED
                    epd.init()
                    epd.Clear(0xFF)
                    epd.sleep()
                elif payload_str == "false":
                    if(next_drawing_available(cur)):
                        system_state = SystemState.DRAWING
                        display_next_drawing(cur, con)
                    else:
                        system_state = SystemState.QR_CODE
                        display_qr_from_disk()

        client.on_subscribe = on_subscribe
        client.on_message = on_message

        if rc != 0:
            print("Failed to connect to MQTT broker: return code %d\n", rc)
            display_text("Failed to connect to MQTT broker: return code " + str(rc))
            return

        # set up subscription
        client.subscribe("epaper/cmnd/#", qos=2)

        # publish a message to let the server know we just booted up
        client.publish("epaper/online", datetime.datetime.utcnow().isoformat())

    def on_disconnect(client, userdata, rc):
        print("on_disconnect called")
        logging.info("Disconnected with result code: %s", rc)
        reconnect_count, reconnect_delay = 0, FIRST_RECONNECT_DELAY
        while True:
            logging.info("Reconnecting in %d seconds...", reconnect_delay)
            time.sleep(reconnect_delay)

            try:
                client.reconnect()
                logging.info("Reconnected successfully!")
                return
            except Exception as err:
                logging.error("%s. Reconnect failed. Retrying...", err)

            reconnect_delay *= RECONNECT_RATE
            reconnect_delay = min(reconnect_delay, MAX_RECONNECT_DELAY)
            reconnect_count += 1

    while True:
        client = mqtt_client.Client(client_id)
        client.username_pw_set(username, password)
        client.tls_set(ca_certs=tls_ca_path)
        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        try:
            client.connect(broker, port)
            return client
        except Exception as error:
            display_text("Connection failed\n" + str(error))
            time.sleep(RECONNECT_RATE)
            # ... then loop again and try to connect
            continue


# ==== Start up epaper display ====
try:
    print("Starting up display")
    epd = epd2in13_V3.EPD()
    epd.init()
    epd.Clear(0xFF)
    # don't go to sleep until init is complete
    # epd.sleep()

    # read font files
    hack16 = ImageFont.truetype("res/Hack.ttf", 16)
except IOError as e:
    logging.error(e)
print("Display ready")
# display_text("Display ready")

try:
    # === Connect to database ===
    con = sqlite3.connect(database_file)
    cur = con.cursor()

    # Start thread for choosing a new image every 5 minutes
    timer_thread = threading.Thread(target=image_timer_loop, args=(10,))
    timer_thread.start()

    # ==== Start MQTT client
    client = mqtt_connect()
    client.loop_forever(timeout=5.0, max_packets=1, retry_first_connection=True)
finally:
    system_state = SystemState.SHUTDOWN
    print("Waiting for timer thread to exit!")
    timer_thread.join()
    con.close()
