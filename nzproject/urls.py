"""集中声明控制面既有 HTTP 路由及同源静态页面入口，未匹配路径返回 JSON 404。"""
from django.urls import path
from revival.views import endpoint, frontend

handler404 = 'revival.views.not_found'

urlpatterns = [
    path('health', endpoint),
    path('api/status', endpoint),
    path('api/login', endpoint),
    path('api/register', endpoint),
    path('api/player/login', endpoint),
    path('api/player/register', endpoint),
    path('api/player/email-code', endpoint),
    path('api/player/password-reset', endpoint),
    path('api/player/email-verify', endpoint),
    path('api/launcher/status', endpoint),
    path('api/launcher/tickets', endpoint),
    path('api/launcher/redeem', endpoint),
    path('api/game/session', endpoint),
    path('api/game/session/renew', endpoint),
    path('api/game/settings/get', endpoint),
    path('api/game/settings/set', endpoint),
    path('api/game/inventory/get', endpoint),
    path('api/game/inventory/equip', endpoint),
    path('api/game/command', endpoint),
    path('api/game/view', endpoint),
    path('api/logout', endpoint),
    path('api/command', endpoint),
    path('api/asset-preview', endpoint),
    path('api/node/heartbeat', endpoint),
    path('api/node/settlement', endpoint),
    path('api/node/ready', endpoint),
    path('api/node/events', endpoint),
    path('api/node/pending', endpoint),
    path('api/node/ack', endpoint),
    path('api/views/<str:view>', endpoint),
    path('', frontend),
    path('assets/<path:asset>', frontend),
]
