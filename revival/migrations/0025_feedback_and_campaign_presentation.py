"""新增反馈/私有截图/公共封面/活动展示表，并将旧活动ID回填为稳定独立地址。"""

import django.db.models.deletion
from django.db import migrations, models


def backfill_presentation(apps, schema_editor):
    """仅追加展示元数据，不修改旧活动奖励、发布快照、账号或资产；可安全重试。"""
    alias = schema_editor.connection.alias
    Campaign = apps.get_model('revival', 'Campaign')
    Presentation = apps.get_model('revival', 'CampaignPresentation')
    for row in Campaign.objects.using(alias).only('id', 'title', 'updated_at').iterator(chunk_size=500):
        Presentation.objects.using(alias).get_or_create(campaign_id=row.id, defaults={
            'slug': row.id, 'title': row.title, 'summary': '', 'theme': 'default',
            'sections': [], 'revision': 1, 'updated_at': row.updated_at})


class Migration(migrations.Migration):
    """先创建五张独立表再回填旧活动；应用回退须保留新表，不反向迁移删除业务数据。"""

    dependencies = [
        ('revival', '0024_email_admin_auth'),
    ]

    operations = [
        migrations.CreateModel(
            name='FeedbackTicket',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=120)),
                ('state', models.CharField(default='OPEN', max_length=16)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('created_at', models.BigIntegerField()),
                ('updated_at', models.BigIntegerField()),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.account')),
            ],
            options={
                'db_table': 'feedback_tickets',
            },
        ),
        migrations.CreateModel(
            name='FeedbackMessage',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('sequence', models.PositiveIntegerField()),
                ('author_kind', models.CharField(max_length=8)),
                ('author_id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32)),
                ('body', models.TextField()),
                ('created_at', models.BigIntegerField()),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.feedbackticket')),
            ],
            options={
                'db_table': 'feedback_messages',
            },
        ),
        migrations.CreateModel(
            name='FeedbackAttachment',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('author_kind', models.CharField(max_length=8)),
                ('author_id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32)),
                ('upload_key', models.CharField(db_collation='utf8mb4_0900_bin', max_length=128)),
                ('input_sha256', models.CharField(max_length=64)),
                ('sha256', models.CharField(max_length=64)),
                ('storage_key', models.CharField(max_length=80)),
                ('mime', models.CharField(max_length=32)),
                ('bytes', models.PositiveIntegerField()),
                ('width', models.PositiveIntegerField()),
                ('height', models.PositiveIntegerField()),
                ('created_at', models.BigIntegerField()),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.feedbackticket')),
            ],
            options={
                'db_table': 'feedback_attachments',
            },
        ),
        migrations.CreateModel(
            name='PublicMedia',
            fields=[
                ('id', models.CharField(db_collation='utf8mb4_0900_bin', max_length=32, primary_key=True, serialize=False)),
                ('upload_key', models.CharField(db_collation='utf8mb4_0900_bin', max_length=128)),
                ('input_sha256', models.CharField(max_length=64)),
                ('sha256', models.CharField(max_length=64)),
                ('storage_key', models.CharField(max_length=80)),
                ('mime', models.CharField(max_length=32)),
                ('bytes', models.PositiveIntegerField()),
                ('width', models.PositiveIntegerField()),
                ('height', models.PositiveIntegerField()),
                ('created_at', models.BigIntegerField()),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.DO_NOTHING, to='revival.admin')),
            ],
            options={
                'db_table': 'public_media',
            },
        ),
        migrations.CreateModel(
            name='CampaignPresentation',
            fields=[
                ('campaign', models.OneToOneField(on_delete=django.db.models.deletion.DO_NOTHING, primary_key=True, serialize=False, to='revival.campaign')),
                ('slug', models.CharField(db_collation='utf8mb4_0900_bin', max_length=96, unique=True)),
                ('title', models.CharField(max_length=120)),
                ('summary', models.CharField(default='', max_length=500)),
                ('theme', models.CharField(default='default', max_length=24)),
                ('sections', models.JSONField(default=list)),
                ('revision', models.PositiveIntegerField(default=1)),
                ('updated_at', models.BigIntegerField()),
                ('cover', models.ForeignKey(null=True, on_delete=django.db.models.deletion.DO_NOTHING, to='revival.publicmedia')),
            ],
            options={
                'db_table': 'campaign_presentations',
            },
        ),
        migrations.AddIndex(
            model_name='feedbackticket',
            index=models.Index(fields=['account', 'updated_at'], name='ix_feedback_owner'),
        ),
        migrations.AddIndex(
            model_name='feedbackticket',
            index=models.Index(fields=['state', 'updated_at'], name='ix_feedback_state'),
        ),
        migrations.AddConstraint(
            model_name='feedbackticket',
            constraint=models.CheckConstraint(condition=models.Q(('state__in', ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'CLOSED'])), name='ck_feedback_state'),
        ),
        migrations.AddIndex(
            model_name='feedbackmessage',
            index=models.Index(fields=['ticket', 'created_at'], name='ix_feedback_messages'),
        ),
        migrations.AddConstraint(
            model_name='feedbackmessage',
            constraint=models.UniqueConstraint(fields=('ticket', 'sequence'), name='uq_feedback_message_order'),
        ),
        migrations.AddConstraint(
            model_name='feedbackattachment',
            constraint=models.UniqueConstraint(fields=('ticket', 'author_kind', 'author_id', 'upload_key'), name='uq_feedback_upload'),
        ),
        migrations.AddConstraint(
            model_name='publicmedia',
            constraint=models.UniqueConstraint(fields=('author', 'upload_key'), name='uq_public_media_upload'),
        ),
        migrations.RunPython(backfill_presentation, migrations.RunPython.noop),
    ]
