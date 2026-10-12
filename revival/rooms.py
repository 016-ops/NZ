"""Room control state and host contract; no real-time game simulation is provided."""
import json
import hmac

from .security import new_token, token_hash
from .service import Fault, canonical, identifier, integer, text_field


def leave_room(service, c, account_id, room_id):
    room = c.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
    c.execute('DELETE FROM room_members WHERE account_id=? AND room_id=?', (account_id, room_id))
    remaining = c.execute('SELECT account_id FROM room_members WHERE room_id=? ORDER BY slot LIMIT 1', (room_id,)).fetchone()
    if not remaining:
        c.execute("UPDATE rooms SET state='CLOSED',revision=revision+1 WHERE id=?", (room_id,))
        if room['match_id']:
            c.execute("UPDATE matches SET state='STOPPED' WHERE id=? AND state IN ('STARTING','ACTIVE')", (room['match_id'],))
    elif room['owner'] == account_id:
        c.execute('UPDATE rooms SET owner=?,revision=revision+1 WHERE id=?', (remaining['account_id'], room_id))
    else:
        c.execute('UPDATE rooms SET revision=revision+1 WHERE id=?', (room_id,))


def room_command(service, c, actor, action, data):
    account_id = actor['id']
    if not c.execute('SELECT 1 FROM characters WHERE account_id=?', (account_id,)).fetchone():
        raise Fault('CHARACTER', 'Initialize a local character first', 409)
    room_id = data.get('room_id')
    if action == 'room.create':
        if c.execute('SELECT 1 FROM room_members WHERE account_id=?', (account_id,)).fetchone():
            raise Fault('ALREADY_IN_ROOM', 'Player already belongs to a room', 409)
        map_key = text_field(data.get('map_key'), 'map_key', 100)
        definition = service.catalog['maps'].get(map_key)
        if definition is None:
            raise Fault('COMPATIBILITY_BLOCKED', 'No verified current-client map/seat rules', 409)
        room_id = identifier()
        c.execute('INSERT INTO rooms(id,owner,map_key,capacity,state) VALUES(?,?,?,?,?)',
                  (room_id, account_id, map_key, definition['capacity'], 'OPEN'))
        c.execute('INSERT INTO room_members VALUES(?,?,0,0,?)', (account_id, room_id, service.clock()))
        return {'room_id': room_id, 'revision': 1, 'state': 'OPEN', 'evidence': service.catalog['evidence']}
    room = c.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
    if room is None or room['state'] == 'CLOSED':
        raise Fault('NOT_FOUND', 'Room is closed or missing', 404)
    member = c.execute('SELECT * FROM room_members WHERE account_id=?', (account_id,)).fetchone()
    if action == 'room.join':
        if member:
            if member['room_id'] == room_id:
                return {'room_id': room_id, 'slot': member['slot'], 'revision': room['revision']}
            raise Fault('ALREADY_IN_ROOM', 'Leave the current room first', 409)
        if room['state'] != 'OPEN':
            raise Fault('ROOM_STATE', 'Room is not open', 409)
        slots = {r['slot'] for r in c.execute('SELECT slot FROM room_members WHERE room_id=?', (room_id,))}
        free = [slot for slot in range(room['capacity']) if slot not in slots]
        if not free:
            raise Fault('ROOM_FULL', 'No free seats', 409)
        c.execute('INSERT INTO room_members VALUES(?,?,?,0,?)', (account_id, room_id, free[0], service.clock()))
        c.execute('UPDATE rooms SET revision=revision+1 WHERE id=?', (room_id,))
        return {'room_id': room_id, 'slot': free[0], 'revision': room['revision'] + 1}
    if not member or member['room_id'] != room_id:
        raise Fault('FORBIDDEN', 'Not a member of this room', 403)
    if action == 'room.leave':
        c.execute('UPDATE match_players SET eligible=0 WHERE account_id=? AND match_id=?', (account_id, room['match_id']))
        leave_room(service, c, account_id, room_id)
        return {'left': True, 'room_id': room_id}
    if action == 'room.ready':
        if room['state'] != 'OPEN' or type(data.get('ready')) is not bool:
            raise Fault('ROOM_STATE', 'Ready requires an open room and boolean value', 409)
        c.execute('UPDATE room_members SET ready=? WHERE account_id=?', (int(data['ready']), account_id))
        c.execute('UPDATE rooms SET revision=revision+1 WHERE id=?', (room_id,))
        return {'room_id': room_id, 'ready': data['ready'], 'revision': room['revision'] + 1}
    if room['owner'] != account_id:
        raise Fault('FORBIDDEN', 'Only the current room owner can manage the room', 403)
    if action == 'room.cancel':
        if room['state'] not in ('ALLOCATING', 'LOADING'):
            raise Fault('ROOM_STATE', 'Cannot cancel a running or open room', 409)
        c.execute("UPDATE matches SET state='STOPPED' WHERE id=?", (room['match_id'],))
        c.execute("UPDATE rooms SET state='OPEN',match_id=NULL,revision=revision+1 WHERE id=?", (room_id,))
        c.execute('UPDATE room_members SET ready=0 WHERE room_id=?', (room_id,))
        return {'room_id': room_id, 'state': 'OPEN', 'revision': room['revision'] + 1}
    if room['state'] != 'OPEN':
        raise Fault('ROOM_STATE', 'Room configuration is frozen after start', 409)
    if data.get('expected_revision') != room['revision']:
        raise Fault('REVISION_CONFLICT', 'Room changed; refresh before retrying', 409)
    if action == 'room.configure':
        map_key = text_field(data.get('map_key'), 'map_key', 100)
        definition = service.catalog['maps'].get(map_key)
        if definition is None:
            raise Fault('COMPATIBILITY_BLOCKED', 'Map is unverified', 409)
        seats = c.execute('SELECT MAX(slot) FROM room_members WHERE room_id=?', (room_id,)).fetchone()[0]
        if seats is not None and seats >= definition['capacity']:
            raise Fault('ROOM_FULL', 'New map cannot hold current seats', 409)
        c.execute('UPDATE rooms SET map_key=?,capacity=?,revision=revision+1 WHERE id=?', (map_key, definition['capacity'], room_id))
        c.execute('UPDATE room_members SET ready=0 WHERE room_id=?', (room_id,))
        return {'room_id': room_id, 'revision': room['revision'] + 1}
    if action == 'room.start':
        members = list(c.execute('SELECT * FROM room_members WHERE room_id=? ORDER BY slot', (room_id,)))
        if not members or any(not p['ready'] for p in members):
            raise Fault('NOT_READY', 'All members must be ready', 409)
        players = []
        for member in members:
            account = service._account(c, member['account_id'])
            service._active(c, account)
            players.append({'account_id': account['id'], 'security_version': account['security_version'],
                            'slot': member['slot'], 'equipment': [dict(r) for r in c.execute(
                                'SELECT e.slot,i.id,i.item_key,i.quantity,i.expires_at FROM equipment e '
                                'JOIN inventory i ON e.inventory_id=i.id WHERE e.account_id=? AND (i.expires_at IS NULL OR i.expires_at>?)',
                                (account['id'], service.clock()))]})
        match_id, instance_id, epoch = identifier(), identifier(), identifier()
        snapshot = {'catalog': service.catalog['id'], 'map_key': room['map_key'], 'revision': room['revision'],
                    'players': players, 'reward': service.catalog.get('reward', {}), 'evidence': service.catalog['evidence']}
        c.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?)',
                  (match_id, room_id, instance_id, epoch, 'STARTING', canonical(snapshot), '', service.clock(), service.clock() + 30))
        for player in players:
            c.execute('INSERT INTO match_players(match_id,account_id,security_version) VALUES(?,?,?)',
                      (match_id, player['account_id'], player['security_version']))
        c.execute("UPDATE rooms SET state='ALLOCATING',match_id=?,revision=revision+1 WHERE id=?", (match_id, room_id))
        return {'match_id': match_id, 'instance_id': instance_id, 'epoch': epoch, 'state': 'STARTING',
                'retail_ready': False, 'evidence': service.catalog['evidence']}
    raise Fault('NOT_FOUND', 'Unknown room action', 404)


def test_host_ready(service, match_id):
    """Private test harness injection, deliberately absent from the HTTP API."""
    if not service.test_profile:
        raise Fault('COMPATIBILITY_BLOCKED', 'No native current-client host is verified', 409)
    with service.db.transaction() as c:
        match = c.execute('SELECT * FROM matches WHERE id=?', (match_id,)).fetchone()
        if not match or match['state'] != 'STARTING' or match['lease_until'] <= service.clock():
            raise Fault('MATCH_STATE', 'Reservation is no longer startable', 409)
        token = new_token()
        c.execute("UPDATE matches SET state='ACTIVE',node_token_hash=?,lease_until=? WHERE id=?",
                  (token_hash(token), service.clock() + 30, match_id))
        c.execute("UPDATE rooms SET state='RUNNING',revision=revision+1 WHERE id=?", (match['room_id'],))
        return {'node_token': token, 'match_id': match_id, 'instance_id': match['instance_id'], 'epoch': match['epoch'], 'evidence': 'TEST_PROFILE'}


def maintain(service):
    with service.db.transaction() as c:
        # Expired sessions cannot retain a seat. This is a local test policy, not a recovered retail rule.
        members = list(c.execute('SELECT account_id FROM room_members WHERE NOT EXISTS '
                                "(SELECT 1 FROM sessions s WHERE s.subject_id=room_members.account_id AND s.kind='player' AND s.revoked=0 AND s.expires_at>?)", (service.clock(),)))
        for member in members:
            service._detach(c, member['account_id'])
        expired = list(c.execute("SELECT id,room_id FROM matches WHERE state IN ('STARTING','ACTIVE') AND lease_until<=?", (service.clock(),)))
        for match in expired:
            c.execute("UPDATE matches SET state='FAILED' WHERE id=?", (match['id'],))
            c.execute("UPDATE rooms SET state='OPEN',match_id=NULL,revision=revision+1 WHERE id=? AND state!='CLOSED'", (match['room_id'],))
            c.execute('UPDATE room_members SET ready=0 WHERE room_id=?', (match['room_id'],))
        return {'expired_matches': len(expired), 'expired_members': len(members)}


def heartbeat(service, token, match_id, epoch):
    with service.db.transaction() as c:
        match = node_identity(service, c, token, match_id, epoch)
        if match['state'] != 'ACTIVE':
            raise Fault('MATCH_STATE', 'Only active hosts can renew leases', 409)
        c.execute('UPDATE matches SET lease_until=? WHERE id=?', (service.clock() + 30, match_id))
        return {'lease_until': service.clock() + 30}


def node_identity(service, c, token, match_id, epoch):
    match = c.execute('SELECT * FROM matches WHERE id=?', (match_id,)).fetchone()
    if not match or not isinstance(token, str) or not match['node_token_hash'] or not hmac.compare_digest(token_hash(token), match['node_token_hash']):
        raise Fault('FORBIDDEN', 'Instance identity rejected', 403)
    if match['epoch'] != epoch or match['lease_until'] <= service.clock():
        raise Fault('STALE_INSTANCE', 'Instance epoch or lease is stale', 409)
    return match
