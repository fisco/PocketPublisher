#The MIT License (MIT)
#
#Copyright (c) 2014 David Fisco
#
#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:
#
#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.
#
#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

import ast
import configparser
import datetime
import html
import json
import os
import sqlite3 as db
import sys
import time
import urllib.parse
import urllib.request

config = configparser.ConfigParser()
config.read('config.ini')
# Handle items in configuration file:
try:
    consumer_key = config['Authentication']['consumer key']
    access_token = config['Authentication']['access token']
    tags_to_file_mappings = []
    for output_file in config['Output Files to Tags Array Mapping']:
        file_dict = { 'tags': ast.literal_eval(config['Output Files to Tags Array Mapping'][output_file]), 'file_name': output_file }
        file_dict['pre_html_file'] = open(config['Pre-HTML Files'][output_file], 'r').read() if (('Pre-HTML Files' in config) and (output_file in config['Pre-HTML Files'])) else ''
        file_dict['html'] = '<dl class="pocketpublisher_items">'
        file_dict['post_html_file'] = open(config['Post-HTML Files'][output_file], 'r').read() if (('Post-HTML Files' in config) and (output_file in config['Post-HTML Files'])) else ''
        file_dict['since'] = int(config['Since Parameters'][output_file]) if (('Since Parameters' in config) and (output_file in config['Since Parameters'])) else int(time.time()) - 31536000
        tags_to_file_mappings.append( file_dict )
    pocket_do_not_publish_tag = config['Pocket Tags']['do not publish tag'] if (('Pocket Tags' in config) and ('do not publish tag' in config['Pocket Tags'])) else 'pp: do not publish'
    pocket_delete_tag = config['Pocket Tags']['delete tag'] if (('Pocket Tags' in config) and ('delete tag' in config['Pocket Tags'])) else 'pp: delete'
except:
    print('You have an error in config.ini.')
    exit()

pocket_data_schema = {
    'item_id': 'integer',
    'time_updated': 'integer',
    'resolved_title': 'string',
    'resolved_url': 'string',
    'excerpt': 'string',
    'is_article': 'integer',
    'has_video': 'integer',
    'has_image': 'integer',
    'tags': 'string',
    'authors': 'string',
    'images': 'string',
    'videos': 'string',
    'word_count': 'integer',
    'favorite': 'integer',
    'delete_on_server': 'integer'
}

conn = db.connect('data.sqlite')
c = conn.cursor()
c.execute('create table if not exists pocket_data (' + ((', '.join("{!s} {!r}".format(key,val) for (key,val) in pocket_data_schema.items())).replace("'", '')) + ')')

post_dict = { 'consumer_key': consumer_key, 'access_token': access_token, 'detailType': 'complete' }
c.execute('select max(time_updated) from pocket_data')
max_time_updated = c.fetchone()[0]
if max_time_updated:
    post_dict['since'] = max_time_updated
data = urllib.parse.urlencode(post_dict)
data = data.encode('utf-8')
request = urllib.request.Request("https://getpocket.com/v3/get")
request.add_header("Content-Type","application/x-www-form-urlencoded;charset=utf-8")
req = urllib.request.urlopen(request, data)
req = json.loads(req.read().decode('utf-8'))

if not req['error'] and len(req['list']):
    # Create a list of Pocket items returned, excluding trashed items:
    items =  [i for i in list(req['list'].values()) if i['status'] != '2']
    # Sort these items on the "time_updated" field:
    items.sort(key=lambda item: int(item['time_updated']))
    for value in items:
        # If item has tag in pocket_delete_tag, set it to be marked for server deletion in the local database.
        if ('tags' in value) and (pocket_delete_tag in value['tags'].keys()):
            value['delete_on_server'] = 1
        # Determine if the item already exists in the local database:
        c.execute('select count(*) from pocket_data where item_id = ' + str(value['item_id']))
        if not c.fetchone()[0]:
            # If it doesn't exist, insert it:
            c.execute("insert into pocket_data(item_id) values (:item_id)", value)
            conn.commit()
        # Now, update it with all available data:
        for key in pocket_data_schema:
            if (key in value) and (key != 'item_id'):
                c.execute("update pocket_data set " + key + "=? where item_id=?", (str(value[key]), str(value['item_id'])))
        conn.commit()

# Delete from the server all items marked for deletion:
ids_to_delete = []
c.row_factory = db.Row
for row in c.execute('select * from pocket_data where delete_on_server=1'):
    ids_to_delete.append(row['item_id'])

for item_id in ids_to_delete:
    request_string = 'https://getpocket.com/v3/send' + '?access_token=' + access_token + '&consumer_key=' + consumer_key + '&actions=' + urllib.parse.quote(json.dumps([{"action": "delete", "item_id": str(item_id)}]))
    request = urllib.request.Request(request_string)
    resp = urllib.request.urlopen(request)
    resp = json.loads(resp.read().decode('utf-8'))
    # If the delete was successful, set 'delete_on_server' to 0 in local database:
    if resp['status'] == 1:
        c.execute("update pocket_data set delete_on_server=0 where item_id=" + str(item_id))
        conn.commit()

c.row_factory = db.Row
for row in c.execute('select * from pocket_data order by time_updated desc'):
    if row['tags']:
        tags_set = set(list(ast.literal_eval(row['tags'])))
        for page in tags_to_file_mappings:
            if (row['time_updated'] > page['since']) and ((len(tags_set - set(page['tags']))) < len(tags_set)) and (pocket_do_not_publish_tag not in tags_set):
                page['html'] += '<dt>'
                page['html'] += '<a href="' + (str(row['resolved_url'])) + '">'
                if row['resolved_title']:
                    page['html'] += (html.escape(str(row['resolved_title'])))
                else:
                    page['html'] += 'UNTITLED'
                page['html'] += '</a>'
                page['html'] += '&nbsp;'
                if int(row['is_article']) > 0:
                    page['html'] += '&nbsp;<i class="fa fa-file-text-o" title="This is an article."></i>'
                if int(row['has_video']) > 0:
                    page['html'] += '&nbsp;<i class="fa fa-video-camera" title="It has a video."></i>'
                page['html'] += '</dt>'
                page['html'] += '<dd>'
                if row['excerpt']:
                    page['html'] += (html.escape(str(row['excerpt'])))
                else:
                    page['html'] += 'NO EXCERPT'
                page['html'] += '<ul>'
                page['html'] += '<li>Posted: <time>' + datetime.datetime.fromtimestamp(int(row['time_updated'])).strftime('%A %d %B %Y') + '</time></li>'
                page['html'] += '<li>Word count: ' + str(row['word_count']) + '</li>'
                page['html'] += '</ul>'
                page['html'] += '</dd>'
for page in tags_to_file_mappings:
    page['html'] += '</dl>'

conn.close()

if not (os.path.isdir('_files')):
    os.makedirs('_files')
os.chdir('_files')
for page in tags_to_file_mappings:
    f = open(page['file_name'],"w")
    f.write(page['pre_html_file'] + page['html'] + page['post_html_file'])
    f.close()