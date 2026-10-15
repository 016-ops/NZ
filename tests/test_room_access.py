"""房间访问规则与列表的实际 MySQL 回归，不作为原客户端房间协议验证。"""
from revival.service import Fault
from .test_control_plane import Base


class RoomAccessTests(Base):
    """校验密码、定向邀请、过期及分页过滤，防止只隐藏前端按钮。"""
    def test_password_invitation_binding_expiry_and_no_hash_leak(self):
        """邀请不能转交另一账号，错误口令/过期邀请不能占席，读模型不泄漏哈希。"""
        a, ta = self.player('room-a')
        b, tb = self.player('room-b')
        _, tc = self.player('room-c')
        room = self.command('room.create', {'map_key': 'test-arena', 'password': 'room-secret-password', 'invite_only': True}, ta)
        with self.assertRaises(Fault):
            self.command('room.join', {'room_id': room['room_id'], 'password': 'room-secret-password'}, tb)
        invite = self.command('room.invite', {'room_id': room['room_id'], 'target_account_id': b, 'expires_at': self.now + 60}, ta)
        with self.assertRaises(Fault):
            self.command('room.join', {'room_id': room['room_id'], 'invitation_token': invite['invitation_token']}, tc)
        self.command('room.join', {'room_id': room['room_id'], 'invitation_token': invite['invitation_token']}, tb)
        self.command('room.leave', {'room_id': room['room_id']}, tb)
        self.now += 61
        with self.assertRaises(Fault):
            self.command('room.join', {'room_id': room['room_id'], 'invitation_token': invite['invitation_token']}, tb)
        view = self.s.query(ta, 'rooms', channel='local', difficulty='default', map_key='test-arena', limit=1)
        self.assertTrue(view[0]['password_required'])
        self.assertNotIn('password_hash', view[0])
        self.assertEqual(self.s.query(ta, 'rooms', offset=1), [])

    def test_password_only_room_requires_password_and_invalid_difficulty_rolls_back(self):
        """公开受密码保护房间按服务端校验，未知难度不能产生半配置的房间。"""
        _, ta = self.player('owner-player')
        _, tb = self.player('other-player')
        with self.assertRaises(Fault):
            self.command('room.create', {'map_key': 'test-arena', 'difficulty': 'invented'}, ta)
        self.assertEqual(self.s.query(ta, 'rooms'), [])
        room = self.command('room.create', {'map_key': 'test-arena', 'password': 'room-secret-password'}, ta)
        with self.assertRaises(Fault):
            self.command('room.join', {'room_id': room['room_id'], 'password': 'wrong'}, tb)
        self.command('room.join', {'room_id': room['room_id'], 'password': 'room-secret-password'}, tb)
