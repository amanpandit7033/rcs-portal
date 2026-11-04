# Generated manually for cutting count tracking on 2026-11-04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reports', '0002_livestatus'),
    ]

    operations = [
        migrations.AddField(
            model_name='livestatus',
            name='cut_count',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Messages stopped from operator submission by cutting',
                verbose_name='Stopped Submissions (Cut)',
            ),
        ),
        migrations.AddField(
            model_name='misbilling',
            name='cut_count',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Messages stopped from operator submission by cutting',
                verbose_name='Stopped Submissions (Cut)',
            ),
        ),
    ]

