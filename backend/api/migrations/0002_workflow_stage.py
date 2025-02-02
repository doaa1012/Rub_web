# Generated by Django 5.0.9 on 2024-09-20 13:24

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Workflow',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='Stage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('typenames', models.JSONField(default=list)),
                ('steps', models.JSONField(blank=True, default=list)),
                ('workflow', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stages', to='api.workflow')),
            ],
        ),
    ]
