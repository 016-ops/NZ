"""集中声明控制面既有 HTTP 路由及同源静态页面入口，未匹配路径返回 JSON 404。"""
from django.urls import path
from revival.views import endpoint, frontend

handler404 = 'revival.views.not_found'

urlpatterns = [
    path('health', endpoint),
    path('api/status', endpoint),
    path('api/login', endpoint),
    path('api/register', endpoint),
    path('api/logout', endpoint),
    path('api/command', endpoint),
    path('api/asset-preview', endpoint),
    path('api/node/heartbeat', endpoint),
    path('api/node/settlement', endpoint),
    path('api/views/<str:view>', endpoint),
    path('', frontend),
    path('assets/<path:asset>', frontend),
]
