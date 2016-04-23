# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rpkidb', '0003_turtle_data'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='parent',
            name='old_parent_handle',
        ),
        migrations.RemoveField(
            model_name='parent',
            name='old_repository',
        ),
        migrations.RemoveField(
            model_name='parent',
            name='old_tenant',
        ),
    ]
