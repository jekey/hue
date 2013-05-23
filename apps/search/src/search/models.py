#!/usr/bin/env python
# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

try:
  import json
except ImportError:
  import simplejson as json

from datetime import datetime
from lxml import etree
import re
import logging

from django.db import models
from django.utils.translation import ugettext as _, ugettext_lazy as _t
from django.core.urlresolvers import reverse
from mako.template import Template

from search.api import SolrApi
from search.conf import SOLR_URL

LOG = logging.getLogger(__name__)



class Facet(models.Model):
  _ATTRIBUTES = ['properties', 'fields', 'ranges', 'dates', 'order']

  enabled = models.BooleanField(default=True)
  data = models.TextField()

  def get_data(self):
    return json.loads(self.data)

  def update_from_post(self, post_data):
    data_dict = json.loads(self.data)

    for attr in Facet._ATTRIBUTES:
      if post_data.get(attr) is not None:
        data_dict[attr] = json.loads(post_data[attr])

    self.data = json.dumps(data_dict)


  def get_query_params(self):
    data_dict = json.loads(self.data)

    properties = data_dict.get('properties')

    params = (
        ('facet', properties.get('isEnabled') and 'true' or 'false'),
        ('facet.limit', properties.get('limit')),
        ('facet.mincount', properties.get('mincount')),
        ('facet.sort', properties.get('sort')),
    )

    if data_dict.get('fields'):
      field_facets = tuple([('facet.field', field_facet['field']) for field_facet in data_dict['fields']])
      params += field_facets

    if data_dict.get('ranges'):
      for field_facet in data_dict['ranges']:
        range_facets = tuple([
                           ('facet.range', field_facet['field']),
                           ('f.%s.facet.range.start' % field_facet['field'], field_facet['start']),
                           ('f.%s.facet.range.end' % field_facet['field'], field_facet['end']),
                           ('f.%s.facet.range.gap' % field_facet['field'], field_facet['gap']),]
                        )
        params += range_facets

    if data_dict.get('dates'):
      for field_facet in data_dict['dates']:
        start = field_facet['start']
        end = field_facet['end']
        gap = field_facet['gap']

        date_facets = tuple([
                           ('facet.date', field_facet['field']),
                           ('f.%s.facet.date.start' % field_facet['field'], 'NOW-%(frequency)s%(unit)s/%(rounder)s' % {"frequency": start["frequency"], "unit": start["unit"], "rounder": gap["unit"]}),
                           ('f.%s.facet.date.end' % field_facet['field'], 'NOW-%(frequency)s%(unit)s' % end),
                           ('f.%s.facet.date.gap' % field_facet['field'], '+%(frequency)s%(unit)s' % gap),]
                        )

        params += date_facets

    return params


class Result(models.Model):
  _ATTRIBUTES = ['properties', 'template', 'highlighting', 'extracode']

  data = models.TextField()

  def update_from_post(self, post_data):
    data_dict = json.loads(self.data)

    for attr in Result._ATTRIBUTES:
      if post_data.get(attr) is not None:
        data_dict[attr] = json.loads(post_data[attr])

    self.data = json.dumps(data_dict)


  def get_template(self, with_highlighting=False):
    data_dict = json.loads(self.data)

    template = data_dict.get('template')
    if with_highlighting:
      for field in data_dict.get('highlighting', []):
        template = re.sub('\{\{%s\}\}' % field, '{{{%s}}}' % field, template)

    return template

  def get_extracode(self):
    data_dict = json.loads(self.data)
    return data_dict.get('extracode')

  def get_highlighting(self):
    data_dict = json.loads(self.data)
    return json.dumps(data_dict.get('highlighting'))

  def get_properties(self):
    data_dict = json.loads(self.data)
    return json.dumps(data_dict.get('properties'))

  def get_query_params(self):
    data_dict = json.loads(self.data)

    params = ()

    if data_dict.get('highlighting'):
      params += (
        ('hl', data_dict.get('properties', {}).get('highlighting_enabled') and 'true' or 'false'),
        ('hl.fl', ','.join(data_dict.get('highlighting'))),
      )

    return params


class Sorting(models.Model):
  _ATTRIBUTES = ['properties', 'fields']

  data = models.TextField()

  def update_from_post(self, post_data):
    data_dict = json.loads(self.data)

    for attr in Sorting._ATTRIBUTES:
      if post_data.get(attr) is not None:
        data_dict[attr] = json.loads(post_data[attr])

    self.data = json.dumps(data_dict)


  def get_query_params(self):
    data_dict = json.loads(self.data)

    params = ()

    if data_dict.get('properties', {}).get('is_enabled') and 'true' or 'false':
      if data_dict.get('fields'):
        fields = ['%s %s' % (field['field'], field['asc'] and 'asc' or 'desc') for field in data_dict.get('fields')]
        params += (
          ('sort', ','.join(fields)),
        )

    return params


  def get_query(self):
    ('sort', solr_query['sort']),


class CollectionManager(models.Manager):

  def get_or_create(self, name, solr_properties, is_core_only=False, is_enabled=True):
    try:
      return self.get(name=name), False
    except Collection.DoesNotExist:
      facets = Facet.objects.create(data=json.dumps({
                   'properties': {'isEnabled': False, 'limit': 10, 'mincount': 1, 'sort': 'count'},
                   'ranges': [],
                   'fields': [],
                   'dates': []
                }))
      result = Result.objects.create(data=json.dumps({
                  'template': """
<div class="row-fluid">
  <div class="span1"><img src="http://twitter.com/api/users/profile_image/{{user_screen_name}}" style="margin:10px"></div>
  <div class="span9">
    <h5><a href="https://twitter.com/{{user_screen_name}}/status/{{id}}" target="_blank" title="Open in Twitter"></a><a target="_blank" href="https://twitter.com/{{user_screen_name}}">{{user_name}}</a></h5>

    {{text}}

    <br>
    <a href="/filebrowser/view/{{file_path}}">
      {{file_name}} {{file_length}}{{#file_length}} bytes {{content_type}}{{/file_length}}
    </a>
    {{#file_path}}
    <a href="/filebrowser/download{{file_path}}?disposition=inline">
      Download
    </a>
    {{/file_path}}
     <div class="stream-item-footer">
        <ul class="tweet-actions">
          <li class="action">
            <a href="https://twitter.com/intent/tweet?in_reply_to={{id}}" target="_blank">
              <i class="icon icon-reply"></i>
              <b>Reply</b>
            </a>
          </li>
          <li class="action">
            <a href="https://twitter.com/intent/retweet?tweet_id={{id}}" target="_blank">
              <i class="icon icon-retweet"></i>
              <b>Retweet</b>
            </a>
          </li>
        </ul>
      </div>

  </div>
  <div class="span2">
    <br><a class="btn" href="https://twitter.com/{{user_screen_name}}/status/{{id}}" target="_blank" title="Open in Twitter">
    <i class="icon-share-alt"></i></a>
    <small class="time">
      <a href="https://twitter.com/{{user_screen_name}}/status/{{id}}" target="_blank" data-dt="{{created_at}}" rel="tooltip" data-placement="left" title="{{created_at}}">{{created_at}}</a>
    </small>
  </div>
</div>
                  """,
                  'highlighting': [],
                  'properties': {'highlighting_enabled': False},
                  'extracode': #"<style>\n</style>\n\n<script>\n</script>"
                  """
<style>
.content {
margin-left: 58px;
}

.action {
margin-right: 5px;
}

.action a, .time a, .account-group, .retweeted {
color: #999999;
}

.account-group a {
text-decoration: none;
font-weight: normal;
}

.username {
font-size: 12px;
}

.time {
color: #BBBBBB;
float: right;
margin-top: 1px;
position: relative;
}

.avatar {
position: absolute;
margin-left: -56px!important;
margin-top: 4px!important;
-webkit-box-shadow: 0 1px 1px rgba(0, 0, 0, .2);
-moz-box-shadow: 0 1px 1px rgba(0, 0, 0, .2);
box-shadow: 0 1px 1px rgba(0, 0, 0, .2);
-webkit-border-radius: 5px;
-moz-border-radius: 5px;
border-radius: 5px;
}

.text {
margin-bottom: 4px;
cursor: pointer;
}

.tweet-actions li {
display: inline;
}

.stream-item-footer {
font-size: 12px;
color: #999999;
}

ul.tweet-actions {
list-style: none outside none;
}

ul.tweet-actions {
margin: 0;
padding: 0;
}

.fullname {
color: #333333;
font-weight: bold;
}

.stream-item-footer, .retweeted {
font-size: 12px;
padding-top: 1px;
}

.icon {
background-position: 0 0;
background-repeat: no-repeat;
display: inline-block;
vertical-align: text-top;
height: 13px;
width: 14px;
margin-top: 0;
margin-left: -2px;
}
.icon-reply {
background-image: url("/search/static/art/reply.png");
}
.icon-retweet {
background-image: url("/search/static/art/retweet.png");
}
.twitter-logo {
background-image: url("/search/static/art/bird_gray_32.png");
width: 32px;
height: 32px;
background-repeat: no-repeat;
display: inline-block;
vertical-align: top;
margin-top: 2px;
}

</style>

<script>
</script>
                  """
              }))
      sorting = Sorting.objects.create(data=json.dumps({'properties': {'is_enabled': False}, 'fields': []}))
      cores = json.dumps(solr_properties)

      return Collection.objects.create(
          name=name,
          label=name,
          enabled=is_enabled,
          cores=cores,
          is_core_only=is_core_only,
          facets=facets,
          result=result,
          sorting=sorting
      ), True


class Collection(models.Model):
  # Perms coming with https://issues.cloudera.org/browse/HUE-950
  enabled = models.BooleanField(default=True)
  name = models.CharField(max_length=40, verbose_name=_t('Solr name'))
  label = models.CharField(max_length=100)
  is_core_only = models.BooleanField(default=False)
  cores = models.TextField(default=json.dumps({}), verbose_name=_t('Collection with cores data'), help_text=_t('Solr json'))
  properties = models.TextField(
      default=json.dumps({}), verbose_name=_t('Properties'),
      help_text=_t('Hue properties (e.g. results by pages number)'))

  facets = models.ForeignKey(Facet)
  result = models.ForeignKey(Result)
  sorting = models.ForeignKey(Sorting)

  objects = CollectionManager()

  def get_query(self):
    return self.facets.get_query_params() + self.result.get_query_params() + self.sorting.get_query_params()

  def get_absolute_url(self):
    return reverse('search:admin_collection', kwargs={'collection_id': self.id})

  @property
  def fields(self):
    return sorted([field.get('name') for field in self.fields_data])

  @property
  def fields_data(self):
    solr_schema = SolrApi(SOLR_URL.get()).schema(self.name)
    schema = etree.fromstring(solr_schema)

    return sorted([{'name': field.get('name'),'type': field.get('type')}
                   for fields in schema.iter('fields') for field in fields.iter('field')])


def get_facet_field_label(field, type, facets):
  label = field
  if type == 'field':
    for fld in facets['fields']:
      if fld['field'] == field:
        label = fld['label']
  elif type == 'range':
    for fld in facets['ranges']:
      if fld['field'] == field:
        label = fld['label']
  elif type == 'date':
    for fld in facets['dates']:
      if fld['field'] == field:
        label = fld['label']
  return label

def get_facet_field_uuid(field, type, facets):
  uuid = ''
  if type == 'field':
    for fld in facets['fields']:
      if fld['field'] == field:
        uuid = fld['uuid']
  elif type == 'range':
    for fld in facets['ranges']:
      if fld['field'] == field:
        uuid = fld['uuid']
  elif type == 'date':
    for fld in facets['dates']:
      if fld['field'] == field:
        uuid = fld['uuid']
  return uuid


def augment_solr_response(response, facets):
  augmented = response
  augmented['normalized_facets'] = []

  normalized_facets = {}
  default_facets = []

  if response and response.get('facet_counts'):
    if response['facet_counts']['facet_fields']:
      for cat in response['facet_counts']['facet_fields']:
        facet = {
          'field': cat,
          'type': 'field',
          'label': get_facet_field_label(cat, 'field', facets),
          'counts': response['facet_counts']['facet_fields'][cat],
        }
        uuid = get_facet_field_uuid(cat, 'field', facets)
        if uuid == '':
          default_facets.append(facet)
        else:
          normalized_facets[uuid] = facet

    if response['facet_counts']['facet_ranges']:
      for cat in response['facet_counts']['facet_ranges']:
        facet = {
          'field': cat,
          'type': 'range',
          'label': get_facet_field_label(cat, 'range', facets),
          'counts': response['facet_counts']['facet_ranges'][cat]['counts'],
          'start': response['facet_counts']['facet_ranges'][cat]['start'],
          'end': response['facet_counts']['facet_ranges'][cat]['end'],
          'gap': response['facet_counts']['facet_ranges'][cat]['gap'],
        }
        uuid = get_facet_field_uuid(cat, 'range', facets)
        if uuid == '':
          default_facets.append(facet)
        else:
          normalized_facets[uuid] = facet

    if response['facet_counts']['facet_dates']:
      for cat in response['facet_counts']['facet_dates']:
        facet = {
          'field': cat,
          'type': 'date',
          'label': get_facet_field_label(cat, 'date', facets),
          'start': response['facet_counts']['facet_dates'][cat]['start'],
          'end': response['facet_counts']['facet_dates'][cat]['end'],
          'gap': response['facet_counts']['facet_dates'][cat]['gap'],
        }
        counts = []
        for date, count in response['facet_counts']['facet_dates'][cat].iteritems():
          if date not in ('start', 'end', 'gap'):
            counts.append(date)
            counts.append(count)
        facet['counts'] = counts

        uuid = get_facet_field_uuid(cat, 'date', facets)
        if uuid == '':
          default_facets.append(facet)
        else:
          normalized_facets[uuid] = facet

  for ordered_uuid in facets.get('order', []):
    try:
      augmented['normalized_facets'].append(normalized_facets[ordered_uuid])
    except:
      pass
  if default_facets:
    augmented['normalized_facets'].extend(default_facets)

  return augmented
