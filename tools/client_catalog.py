"""Read-only, evidence-preserving NZ client catalogue extraction.

Encrypted INI decoder format studied in af-emulator (MIT); see
third_party/af-emulator/LICENSE. No patch/install routines are imported or run.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path

FORMAT_VERSION = 1
MAGIC = bytes.fromhex('f3f3f3f3')
# Resource-format constant from the pinned MIT research tool, not a login key.
RESOURCE_KEY = bytes.fromhex('5447414d451fe92c9a971a0cd1f610fb')
TEXT_EXTENSIONS = {'.ini', '.cht', '.int', '.xml', '.json', '.txt', '.cfg', '.lua', '.csv'}
PACKAGE_EXTENSIONS = {'.u', '.upk', '.udk', '.umap', '.pak', '.tfc', '.vfs'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def identity(*parts):
    return hashlib.sha256(canonical(parts).encode('utf-8')).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def decode_resource(blob, limit=64 * 1024 * 1024):
    """Strict bounded AES/zlib decoding and lossless text decoding."""
    if not blob:
        raise ValueError('EMPTY_FILE')
    if not any(blob):
        raise ValueError('ZERO_FILLED')
    encrypted = blob.startswith(MAGIC)
    if encrypted:
        if len(blob) <= 4 or (len(blob) - 4) % 16:
            raise ValueError('INVALID_AES_BLOCK_LENGTH')
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        decoder = Cipher(algorithms.AES(RESOURCE_KEY), modes.ECB()).decryptor()
        payload = decoder.update(blob[4:]) + decoder.finalize()
        expected = struct.unpack_from('<I', payload)[0]
        if expected > limit:
            raise ValueError('DECOMPRESSED_SIZE_LIMIT')
        inflater = zlib.decompressobj()
        blob = inflater.decompress(payload[4:], expected + 1)
        if len(blob) != expected or not inflater.eof or inflater.unconsumed_tail:
            raise ValueError('DECOMPRESSED_LENGTH_MISMATCH')
        # Existing resource writers do not require zero padding. zlib's EOF and
        # declared clear length delimit content; do not reject valid nonzero tails.
        padding = inflater.unused_data
    if len(blob) > limit:
        raise ValueError('TEXT_SIZE_LIMIT')
    original_blob = blob
    utf16 = blob.startswith((b'\xff\xfe', b'\xfe\xff'))
    terminal_nuls = 0 if utf16 else len(blob) - len(blob.rstrip(b'\0'))
    if terminal_nuls: blob = blob[:-terminal_nuls]
    encodings = ['utf-16'] if utf16 else ['utf-8-sig', 'gb18030']
    if not utf16 and re.search(rb'(?m)^\[[^\]\r\n]{1,255}\]\r?$', blob):
        high_bytes = {c for c in blob if c >= 128}
        # Western smart punctuation surrounded by ASCII is otherwise sometimes
        # accidentally valid as a GB double-byte sequence. Record the fallback.
        if high_bytes and all(128 <= c < 160 for c in high_bytes):
            encodings.insert(1, 'cp1252')
        else:
            encodings.append('cp1252')
    for encoding in encodings:
        try:
            text = blob.decode(encoding, errors='strict')
        except UnicodeError:
            continue
        if '\x00' in text:
            raise ValueError('BINARY_OR_NUL_TEXT')
        if any(ord(c) < 32 and c not in '\r\n\t' for c in text):
            raise ValueError('CONTROL_CHARACTER_TEXT')
        return text, {'encoding': encoding, 'encrypted': encrypted,
                      'padding_bytes': len(padding) if encrypted else 0,
                      'padding_sha256': hashlib.sha256(padding).hexdigest() if encrypted else None,
                      'terminal_nul_bytes': terminal_nuls, 'encoding_fallback': encoding == 'cp1252',
                      'decoded_bytes': len(original_blob), 'decoded_sha256': hashlib.sha256(original_blob).hexdigest()}
    raise ValueError('UNKNOWN_ENCODING')


def split_top(text, delimiter=','):
    """Split Unreal tuple syntax, respecting nesting and escaped quotes."""
    result, start, depth, quote, escaped = [], 0, 0, None, False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char == '"':
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise ValueError('UNBALANCED_TUPLE')
        elif char == delimiter and depth == 0:
            result.append(text[start:index].strip())
            start = index + 1
    if quote or depth:
        raise ValueError('UNTERMINATED_VALUE')
    result.append(text[start:].strip())
    return result


def parse_value(raw):
    raw = raw.strip()
    if not raw:
        return ''
    if raw.startswith('"'):
        if not raw.endswith('"') or len(split_top(raw)) != 1:
            raise ValueError('UNTERMINATED_QUOTED_VALUE')
        # Preserve unknown escapes (including resource paths), interpret quoted quotes only.
        return raw[1:-1].replace('\\"', '"')
    if raw.startswith('('):
        if not raw.endswith(')'):
            raise ValueError('UNTERMINATED_TUPLE')
        inner = raw[1:-1].strip()
        if not inner:
            return []
        parts = split_top(inner)
        pairs = [split_top(part, '=') for part in parts]
        if all(len(pair) == 2 and re.fullmatch(r'[A-Za-z_][\w.\[\]]*', pair[0]) for pair in pairs):
            return {'fields': [{'key': pair[0], 'value': parse_value(pair[1])} for pair in pairs]}
        return [parse_value(part) for part in parts]
    if raw.lower() in ('true', 'false'):
        return raw.lower() == 'true'
    if re.fullmatch(r'[+-]?\d+', raw):
        return int(raw)
    if re.fullmatch(r'[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fF]?', raw) and ('.' in raw or 'e' in raw.lower() or raw.endswith(('f', 'F'))):
        return float(raw.rstrip('fF'))
    return raw


def strip_inline_comment(text):
    quote, escaped, depth = False, False, 0
    for index, char in enumerate(text):
        if quote:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quote = False
        elif char == '"': quote = True
        elif char == '(': depth += 1
        elif char == ')': depth -= 1
        elif char == ';' and depth == 0:
            return text[:index].rstrip(), text[index:]
    return text, None


def fields(value):
    """Convenience view; lossless duplicate fields remain in the AST."""
    return {x['key']: x['value'] for x in value.get('fields', [])} if isinstance(value, dict) else {}


def parse_ini(text, path):
    records, section, section_occurrence = [], '', 0
    sections, occurrences = collections.Counter(), collections.Counter()
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        start = index + 1
        raw = lines[index]
        stripped = raw.strip()
        index += 1
        inline_comment = None
        if not stripped or stripped.startswith((';', '#', '//')):
            kind, key, value, operator = 'comment' if stripped else 'blank', None, None, ''
        elif stripped.startswith('[') and ']' in stripped:
            section = stripped[1:stripped.index(']')]
            sections[section] += 1
            section_occurrence = sections[section]
            kind, key, value, operator = 'section', None, section, ''
        elif '=' in stripped:
            kind = 'assignment'
            left, right = stripped.split('=', 1)
            left = left.strip()
            operator = left[0] if left[:1] in ('+', '.', '-', '!') else ''
            key = left[1:] if operator else left
            # A trailing semicolon is common in BasedOn/default declarations.
            right, inline_comment = strip_inline_comment(right.strip())
            while True:
                try:
                    split_top(right)
                    break
                except ValueError as error:
                    if str(error) != 'UNTERMINATED_VALUE' or index >= len(lines) or lines[index].lstrip().startswith('['):
                        break
                    raw += '\n' + lines[index]
                    right += '\n' + lines[index]
                    index += 1
            try:
                value = parse_value(right)
            except (ValueError, RecursionError, OverflowError) as error:
                value = {'unparsed': right, 'error': str(error)}
                kind = 'unparsed_assignment'
        else:
            kind, key, value, operator = 'unparsed_line', None, stripped, ''
        occurrences[(section, key)] += 1
        records.append({'id': identity(path, start), 'source_path': path, 'line': start, 'line_end': index,
                        'section': section, 'section_occurrence': section_occurrence, 'kind': kind,
                        'operator': operator, 'key': key, 'occurrence': occurrences[(section, key)],
                        'value': value, 'inline_comment': inline_comment, 'raw': raw})
    return records


def nested_refs(value, trail=''):
    if isinstance(value, dict) and 'fields' in value:
        for member in value['fields']:
            key, val = member['key'], member['value']
            name = trail + '.' + key if trail else key
            if re.search(r'(?:Item|Weapon|Prop|AwardItem|Commodity)(?:ID|Id)(?:Array)?$', key):
                values = val if isinstance(val, list) else [val]
                for v in values:
                    if isinstance(v, (int, str)) and not isinstance(v, bool) and str(v) not in ('0', '-1', ''):
                        yield ('shop' if 'commodity' in key.lower() else 'item'), str(v), name
            yield from nested_refs(val, name)
    elif isinstance(value, list):
        for i, member in enumerate(value):
            yield from nested_refs(member, f'{trail}[{i}]')


def pick(data, *keys):
    for key in keys:
        if key in data:
            return data[key]
    return None


class Catalogue:
    def __init__(self, source_files, records):
        self.files, self.records = source_files, records
        self.entities, self.relations, self.issues = {}, {}, []
        self.items, self.shop, self.options, self.shop_items = [], [], [], []
        self.weapons, self.modes, self.maps, self.features, self.rules, self.currencies = [], [], [], [], [], []
        self.evidence = []
        self._issue_keys = set()
        self._evidence_keys = set()

    def issue(self, code, path=None, record_id=None, detail=None):
        key = identity(code, path, record_id, detail)
        if key not in self._issue_keys:
            self._issue_keys.add(key)
            self.issues.append({'id': key, 'code': code, 'source_path': path,
                                'record_id': record_id, 'detail': detail})

    def entity(self, category, key, name=None, native_id=None, description=None, status='CLIENT_DEFINITION', record=None, path=None):
        entity_id = category + ':' + str(key)
        if len(entity_id) > 180 or not entity_id.isascii():
            entity_id = category + ':keyhash:' + identity(key)
        if entity_id not in self.entities:
            self.entities[entity_id] = {'id': entity_id, 'category': category, 'native_id': native_id,
                                         'name': name or str(key), 'description': description,
                                         'evidence_status': status, 'runtime_status': 'NOT_VALIDATED'}
        elif name and self.entities[entity_id]['name'] != name:
            self.issue('NAME_CONFLICT', path, record and record['id'], {'entity_id': entity_id, 'names': [self.entities[entity_id]['name'], name]})
        ev = {'id': identity(entity_id, record and record['id'], path or (record and record['source_path'])),
              'entity_id': entity_id, 'record_id': record and record['id'],
              'source_path': path or (record and record['source_path']), 'status': status}
        if ev['id'] not in self._evidence_keys:
            self._evidence_keys.add(ev['id']); self.evidence.append(ev)
        return entity_id

    def relation(self, source, target, kind, record, raw_target=None):
        rid = identity(source, target, kind, record and record['id'], raw_target)
        self.relations[rid] = {'id': rid, 'source_id': source, 'target_id': target, 'relation_type': kind,
                               'record_id': record and record['id'], 'raw_target': raw_target,
                               'status': 'RESOLVED' if target else 'UNRESOLVED'}
        if not target:
            self.issue('UNRESOLVED_REFERENCE', record and record['source_path'], record and record['id'],
                       {'source_id': source, 'relation_type': kind, 'raw_target': raw_target})

    def build(self):
        # Keep physical packages distinct from logical maps and gameplay definitions.
        for file in self.files:
            if file['status'] not in ('PARSED_TEXT', 'FILE_INDEX_ONLY'):
                self.issue(file['status'], file['path'], detail=file.get('error'))
            if file.get('terminal_nul_bytes'):
                self.issue('TEXT_TERMINAL_NUL_PADDING', file['path'], detail={'bytes': file['terminal_nul_bytes'], 'content': 'Known zero padding retained in metadata; source untouched'})
            if file.get('encoding_fallback'):
                self.issue('LEGACY_WESTERN_ENCODING', file['path'], detail={'encoding': file['encoding'], 'interpretation': 'Lossless fallback, not a Chinese-name inference'})
            if file['extension'] in ('.udk', '.umap'):
                eid = self.entity('map_resource', file['path'], Path(file['path']).stem,
                                  status='RESOURCE_CLUE', path=file['path'])
                self.maps.append({'entity_id': eid, 'source_path': file['path'], 'logical_map_id': None,
                                  'player_limit': None, 'difficulty': None, 'attributes': {}})
        assignments = [r for r in self.records if r['kind'] == 'assignment']
        for r in self.records:
            if r['kind'].startswith('unparsed'):
                self.issue('UNPARSED_CONFIG', r['source_path'], r['id'], r['value'])
        # Pass 1: stable native item/commodity IDs before linking references.
        for r in assignments:
            d = fields(r['value']); p, key = r['source_path'], r['key']
            if Path(p).name == 'DefaultItemLibrary.ini' and key in ('ItemDatas', 'RawItemDatas'):
                native = d.get('nItemID')
                if native is None:
                    self.issue('MISSING_ITEM_ID', p, r['id']); continue
                eid = self.entity('item', native, pick(d, 'ItemChineseName', 'ItemName'), str(native), d.get('Description'), record=r)
                if any(x['entity_id'] == eid for x in self.items):
                    self.issue('DUPLICATE_ITEM_ID', p, r['id'], native)
                self.items.append({'id': r['id'], 'entity_id': eid, 'record_id': r['id'],
                                   'internal_name': d.get('ItemName'), 'classification': 'UNCONFIRMED',
                                   'location': d.get('Location'), 'consume_type': d.get('ConsumeType'),
                                   'hidden': d.get('bHidden'), 'stack_limit': d.get('StackNum'),
                                   'icon_id': d.get('nItemIconID'), 'attributes': r['value']})
            elif Path(p).name == 'DefaultCommodityLibrary.ini' and key == 'RawCommodityDatas':
                native = d.get('nCommodityID')
                if native is None:
                    self.issue('MISSING_COMMODITY_ID', p, r['id']); continue
                eid = self.entity('shop', native, d.get('sItemName'), str(native), d.get('sItemDescription'), record=r)
                if any(x['entity_id'] == eid for x in self.shop):
                    self.issue('DUPLICATE_COMMODITY_ID', p, r['id'], native)
                currency = d.get('sCommodityType')
                currency_id = self.entity('currency', currency, currency, record=r) if currency else None
                self.shop.append({'id': r['id'], 'entity_id': eid, 'record_id': r['id'],
                                  'currency_id': currency_id, 'is_package': d.get('bIsPackage'),
                                  'show_commodity': d.get('bShowCommodity'), 'sale_tag': d.get('nSaleTag'),
                                  'sale_date_raw': d.get('ItemOnSaleDate'), 'recall_date_raw': d.get('ItemRecallDate'),
                                  'buy_grade': d.get('nBuyGrade'), 'attributes': r['value']})
                arrays = [d.get('nPriceArray', []), d.get('nAvailPreiodArray', []), d.get('PeriodArrayForSuite', [])]
                arrays = [x if isinstance(x, list) else [x] for x in arrays]
                if len(arrays[0]) != len(arrays[1]):
                    self.issue('SHOP_OPTION_ARRAY_LENGTH', p, r['id'], {'price_length': len(arrays[0]), 'period_length': len(arrays[1])})
                if arrays[2] and len(arrays[2]) != len(arrays[0]):
                    self.issue('SHOP_SUITE_ARRAY_LENGTH', p, r['id'], {'lengths': [len(x) for x in arrays],
                               'interpretation': 'Optional suite array differs; meaning unconfirmed, not a price/period mismatch'})
                for index in range(max(map(len, arrays))):
                    self.options.append({'id': identity(r['id'], 'option', index), 'product_id': eid,
                                         'record_id': r['id'], 'option_index': index,
                                         'price_raw': arrays[0][index] if index < len(arrays[0]) else None,
                                         'period_raw': arrays[1][index] if index < len(arrays[1]) else None,
                                         'suite_period_raw': arrays[2][index] if index < len(arrays[2]) else None,
                                         'period_unit': None, 'interpretation': 'RAW_CLIENT_VALUES'})
        # Item classification requires a local weapon class reference, never an ID range guess.
        by_name = collections.defaultdict(list)
        for x in self.items:
            if x['internal_name']: by_name[x['internal_name'].lower()].append(x)
        section_rows = collections.defaultdict(list)
        for r in self.records:
            section_rows[(r['source_path'], r['section'])].append(r)
        for (p, section), rows in sorted(section_rows.items()):
            active = [r for r in rows if r['kind'] == 'assignment']
            if not section: continue
            basename, token = Path(p).name, section.split('.')[-1]
            is_config = '/Config/' in '/' + p
            is_weapon = is_config and ('Weapon' in basename or 'Weap' in basename)
            if is_weapon:
                eid = self.entity('weapon_class', section, section, record=next((r for r in rows if r['kind'] == 'section'), rows[0]))
                self.weapons.append({'id': identity(p, section), 'entity_id': eid, 'source_path': p,
                                     'class_name': section, 'item_ids': [], 'record_ids': [r['id'] for r in active],
                                     'attributes': [{'key': r['key'], 'operator': r['operator'], 'value': r['value']} for r in active]})
                candidates = {token.lower(), re.sub(r'^(?:TG|UT|PVE)?(?:Weap|Weapon)_?', '', token, flags=re.I).lower()}
                for candidate in candidates:
                    for item in by_name.get(candidate, []):
                        item['classification'] = 'WEAPON_CLASS_MATCH'
                        self.weapons[-1]['item_ids'].append(item['entity_id'])
                        self.relation(item['entity_id'], eid, 'WEAPON_CLASS_NAME_MATCH', rows[0])
            # Every configuration section remains accessible as a functional/rule group.
            if is_config and not is_weapon:
                eid = self.entity('feature', section, section, record=next((r for r in rows if r['kind'] == 'section'), rows[0]))
                self.features.append({'id': identity(p, section), 'entity_id': eid, 'source_path': p,
                                      'class_name': section, 'record_ids': [r['id'] for r in active],
                                      'attributes': [{'key': r['key'], 'operator': r['operator'], 'record_id': r['id']} for r in active]})
            elif not is_config:
                eid = None
            for r in active:
                if is_config:
                    self.rules.append({'id': r['id'], 'entity_id': eid, 'record_id': r['id'],
                                       'key': r['key'], 'operator': r['operator'], 'value': r['value']})
        # Explicit prefix/type definitions are local mode evidence, not protocol ID validation.
        mode_classes = {}
        for r in assignments:
            d = fields(r['value'])
            if r['key'] == 'GameModeSettings' and d.get('ModeName'):
                cls = d['ModeName']; mid = self.entity('mode', cls, cls, record=r)
                mode_classes[cls] = mid
                self.modes.append({'id': r['id'], 'entity_id': mid, 'record_id': r['id'],
                                   'prefix': None, 'class_name': cls, 'mode_id': d.get('ModeId'),
                                   'player_limit': None, 'difficulty': None, 'attributes': r['value']})
                fid = 'feature:' + cls
                if fid in self.entities: self.relation(mid, fid, 'MODE_CONFIG_CLASS', r)
            if r['key'] == 'DefaultMapPrefixes' and d.get('GameType'):
                cls = d['GameType']; mid = self.entity('mode', cls, cls, record=r)
                mode_classes[cls] = mid
                self.modes.append({'id': r['id'], 'entity_id': mid, 'record_id': r['id'],
                                   'prefix': d.get('Prefix'), 'class_name': cls,
                                   'mode_id': None, 'player_limit': None, 'difficulty': None,
                                   'attributes': r['value']})
                fid = 'feature:' + cls
                if fid in self.entities: self.relation(mid, fid, 'MODE_CONFIG_CLASS', r)
            # Other records include real mode/map IDs and names; keep each row as a definition.
            if d and r['key'] == 'GameMapSettings':
                native = pick(d, 'MapID', 'MapId', 'nMapID')
                logical = native if native is not None else identity(r['source_path'], r['id'])
                eid = self.entity('map', logical, pick(d, 'NickName', 'MapDisplayName', 'MapName', 'MapFileName') or str(logical),
                                  str(native) if native is not None else None, record=r)
                self.maps.append({'entity_id': eid, 'record_id': r['id'], 'source_path': r['source_path'],
                                  'logical_map_id': native, 'player_limit': pick(d, 'MaxPlayers', 'PlayerNum'),
                                  'difficulty': pick(d, 'Difficulty', 'DifficultyLevel'), 'attributes': r['value']})
                map_name = pick(d, 'MapName', 'MapFileName')
                resources = [file for file in self.files if file['extension'] in ('.udk', '.umap') and Path(file['path']).stem == map_name]
                for file in resources:
                    self.relation(eid, 'map_resource:' + file['path'], 'MAP_EXACT_RESOURCE_NAME', r)
                if not resources:
                    self.issue('MAP_RESOURCE_NOT_FOUND', r['source_path'], r['id'], {'entity_id': eid, 'map_name': map_name})
                # Prefix association is a configuration clue, not proof that the server permits this pair.
                for mode in self.modes:
                    if mode['prefix'] and isinstance(map_name, str) and map_name.startswith(mode['prefix'] + '-'):
                        self.relation(eid, mode['entity_id'], 'MAP_PREFIX_MODE_CLUE', r, mode['prefix'])
        # Localized UI sections are also functional evidence. Keep their entire text,
        # including menus not represented in the surviving configuration sections.
        for (p, section), rows in sorted(section_rows.items()):
            if '/Localization/' not in '/' + p or not section: continue
            package = Path(p).stem
            cls = package + '.' + section
            targets = [eid for eid in self.entities if eid in ('feature:' + cls, 'weapon_class:' + cls, 'mode:' + cls)]
            if not targets:
                eid = self.entity('localized_section', cls, cls, record=rows[0])
                self.features.append({'id': identity(p, section), 'entity_id': eid, 'source_path': p,
                                      'class_name': cls, 'record_ids': [r['id'] for r in rows if r['kind'] == 'assignment'],
                                      'attributes': [{'key': r['key'], 'operator': r['operator'], 'record_id': r['id']} for r in rows if r['kind'] == 'assignment']})
                targets = [eid]
            for target in targets:
                self.entity(self.entities[target]['category'], target.split(':', 1)[1], record=rows[0])
                for r in rows:
                    if r['kind'] == 'assignment' and r['key'] in ('GameName', 'Title', 'FriendlyName', 'WindowTitle') and isinstance(r['value'], str):
                        self.entities[target].setdefault('localized_names', []).append({'name': r['value'], 'record_id': r['id']})
        # Attach mode-specific class settings, without applying global defaults or
        # guessing class inheritance from names.
        for mode in self.modes:
            active = [r for (p, sec), rs in section_rows.items() if sec == mode['class_name'] and '/Config/' in '/' + p
                      for r in rs if r['kind'] == 'assignment']
            mode['rule_record_ids'] = [r['id'] for r in active]
            for r in active:
                if r['key'] in ('MaxPlayers', 'MaxClients', 'MaxClientPlayers') and isinstance(r['value'], int):
                    mode['player_limit'] = r['value']
                if r['key'] in ('GameDifficulty', 'Difficulty'):
                    mode['difficulty'] = r['value']
        # Explicit item references throughout rules, drops, quests and shop bundles.
        mode_ids = collections.defaultdict(set)
        for mode in self.modes:
            if mode['mode_id'] is not None: mode_ids[str(mode['mode_id'])].add(mode['entity_id'])
        for r in assignments:
            d = fields(r['value'])
            if Path(r['source_path']).name == 'DefaultCommodityLibrary.ini' and r['key'] == 'RawCommodityDatas':
                source = 'shop:' + str(d.get('nCommodityID'))
                ids = d.get('nItemIDArray', [])
                ids = ids if isinstance(ids, list) else [ids]
                for index, native in enumerate(ids):
                    target = 'item:' + str(native)
                    target = target if target in self.entities else None
                    self.shop_items.append({'id': identity(r['id'], 'item', index), 'product_id': source,
                                            'record_id': r['id'], 'item_index': index, 'item_id': target,
                                            'native_item_id': str(native), 'quantity': None})
                    self.relation(source, target, 'SHOP_CONTAINS_ITEM', r, str(native))
            else:
                source = 'item:' + str(d.get('nItemID')) if r['key'] in ('ItemDatas', 'RawItemDatas') else 'feature:' + r['section']
                if source not in self.entities: continue
                for category, native, member in nested_refs(r['value']):
                    target = category + ':' + native
                    self.relation(source, target if target in self.entities else None, member, r, native)
                native_map = pick(d, 'MapID', 'MapId', 'nMapID')
                if native_map is not None and r['key'] != 'GameMapSettings':
                    target = 'map:' + str(native_map)
                    self.relation(source, target if target in self.entities else None, 'CONFIG_MAP_REFERENCE', r, str(native_map))
                native_mode = pick(d, 'ModeID', 'ModeId', 'Modeid', 'GameModeID')
                if native_mode is not None and r['key'] != 'GameModeSettings':
                    matches = mode_ids.get(str(native_mode), set())
                    for target in matches: self.relation(source, target, 'CONFIG_MODE_REFERENCE', r, str(native_mode))
                    if not matches: self.relation(source, None, 'CONFIG_MODE_REFERENCE', r, str(native_mode))
        for currency in [e for e in self.entities.values() if e['category'] == 'currency']:
            self.currencies.append({'entity_id': currency['id'], 'code': currency['name'],
                                    'display_name': None, 'exchange_rate': None, 'attributes': {}})


def scan(root, progress=True):
    root = root.resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor) or root.is_symlink() or root.is_junction():
        raise ValueError('Client root must be a real non-root directory')
    files, records, stats = [], [], {}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for name in dirs:
            child = Path(base) / name
            if child.is_symlink() or child.is_junction(): raise ValueError('Client reparse directory: ' + str(child))
        for name in sorted(names):
            path = Path(base) / name
            if path.is_symlink(): raise ValueError('Client symlink file: ' + str(path))
            before = path.stat(); relative = path.relative_to(root).as_posix()
            ext = path.suffix.lower()
            row = {'id': identity(relative), 'path': relative, 'bytes': before.st_size,
                   'sha256': file_hash(path), 'extension': ext, 'status': 'FILE_INDEX_ONLY',
                   'parser': None, 'error': None, 'record_count': 0}
            # Metadata only for launchers, account caches, logs and platform components.
            game_content = relative.startswith(('TGame/', 'Engine/Config/'))
            if game_content and ext in TEXT_EXTENSIONS:
                if before.st_size > 64 * 1024 * 1024:
                    row.update(status='TEXT_SIZE_LIMIT', error='Read limit 64 MiB')
                else:
                    try:
                        text, meta = decode_resource(path.read_bytes())
                        row.update(meta)
                        if ext in ('.ini', '.cht', '.int', '.cfg'):
                            parsed = parse_ini(text, relative)
                            records.extend(parsed)
                            row.update(status='PARSED_TEXT', parser='unreal-ini-v1', record_count=len(parsed))
                        else:
                            # Other readable assets retained in full without assuming an INI schema.
                            parsed_value = json.loads(text) if ext == '.json' else {'text': text}
                            rid = identity(relative, 1)
                            records.append({'id': rid, 'source_path': relative, 'line': 1, 'line_end': len(text.splitlines()),
                                            'section': '', 'section_occurrence': 0, 'kind': 'text_document',
                                            'operator': '', 'key': None, 'occurrence': 1, 'value': parsed_value, 'raw': text})
                            row.update(status='PARSED_TEXT', parser='json-v1' if ext == '.json' else 'text-v1', record_count=1)
                    except (ValueError, UnicodeError, zlib.error, struct.error) as error:
                        row.update(status='ZERO_FILLED' if str(error) == 'ZERO_FILLED' else 'DECODE_FAILED', error=str(error))
            elif game_content and ext in PACKAGE_EXTENSIONS | {'.tdr', '.bin', '.dat'}:
                # Inspect all bytes during hashing, and explicitly expose unavailable semantic parsing.
                row.update(status='PACKAGE_UNDECODED' if ext in PACKAGE_EXTENSIONS else 'BINARY_DEFINITION_UNDECODED')
                if before.st_size and before.st_size <= 64 * 1024 * 1024:
                    with path.open('rb') as stream:
                        first = stream.read(4096)
                    if not any(first) and not any(path.read_bytes()): row['status'] = 'ZERO_FILLED'
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Client changed during scan: ' + relative)
            files.append(row)
            stats[relative] = (before.st_size, before.st_mtime_ns)
            if progress and len(files) % 1000 == 0:
                print(canonical({'phase': 'scan', 'files': len(files)}), flush=True)
    if not files: raise ValueError('Empty client root')
    return files, records, stats


def check_unchanged(root, files, stats):
    """Second streaming hash pass, also detects added/removed files."""
    actual = {p.relative_to(root).as_posix(): p for p in root.rglob('*') if p.is_file()}
    if set(actual) != set(stats): raise ValueError('Client file set changed during extraction')
    for index, row in enumerate(files, 1):
        path = actual[row['path']]; stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns) != stats[row['path']] or file_hash(path) != row['sha256']:
            raise ValueError('Client changed after extraction: ' + row['path'])
        if index % 2000 == 0: print(canonical({'phase': 'verify_client', 'files': index}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-root', type=Path)
    parser.add_argument('--rebuild-from', type=Path, help='Reparse captured text from a verified prior delivery without rereading binaries')
    parser.add_argument('--refresh-failed-text-from', type=Path, help='During rebuild, retry failed text files after checking their captured SHA256')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--external-evidence', type=Path, default=Path(__file__).resolve().parents[1] / 'config/client-catalog-external.json')
    args = parser.parse_args()
    output = args.output.resolve()
    if bool(args.client_root) == bool(args.rebuild_from):
        raise ValueError('Choose exactly one of --client-root and --rebuild-from')
    if args.refresh_failed_text_from and not args.rebuild_from:
        raise ValueError('--refresh-failed-text-from requires --rebuild-from')
    if args.client_root:
        root = args.client_root.resolve(strict=True)
        if output == root or root in output.parents or output in root.parents:
            raise ValueError('Output must be outside and must not contain the client directory')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new empty output directory; published catalogues are immutable')
    from tools.client_catalog_export import export_catalogue, add_external, verify_output
    from tools.client_catalog_export import load_capture
    if args.client_root:
        source_files, records, stats = scan(root)
        check_unchanged(root, source_files, stats)
        capture = None
    else:
        source_files, records, capture = load_capture(args.rebuild_from.resolve(), args.refresh_failed_text_from)
    external = json.loads(args.external_evidence.read_text(encoding='utf-8')) if args.external_evidence.exists() else None
    definition_hash = identity(FORMAT_VERSION, file_hash(Path(__file__)),
                               file_hash(Path(__file__).with_name('client_catalog_export.py')), external)
    snapshot_id = identity([(f['path'], f['bytes'], f['sha256']) for f in source_files], definition_hash)
    catalog = Catalogue(source_files, records); catalog.build()
    catalog.definition_hash = definition_hash
    catalog.capture_metadata = capture
    if external: add_external(catalog, external)
    output.mkdir(parents=True, exist_ok=True)
    report = export_catalogue(catalog, snapshot_id, output)
    verification = verify_output(output)
    if not verification['passed']: raise ValueError('Output verification failed: ' + canonical(verification))
    print(canonical({'output': str(output), 'snapshot_id': snapshot_id, 'counts': report['counts'], 'verification': verification}), flush=True)


if __name__ == '__main__':
    # Supports both python -m tools.client_catalog and python tools/client_catalog.py.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
