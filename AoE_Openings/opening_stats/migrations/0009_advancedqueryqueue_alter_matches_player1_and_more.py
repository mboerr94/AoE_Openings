# Generated by Django 4.0 on 2022-02-08 05:09

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('opening_stats', '0008_auto_20211129_0459'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdvancedQueryQueue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('time', models.DateTimeField(auto_now_add=True)),
                ('time_completed', models.DateTimeField(blank=True)),
                ('last_checkin', models.DateTimeField(auto_now_add=True)),
                ('stale', models.BooleanField(blank=True, default=False)),
                ('result', models.TextField(blank=True)),
                ('query', models.TextField()),
            ],
            options={
                'db_table': 'advanced_query_queue',
            },
        ),
        migrations.AlterField(
            model_name='matches',
            name='player1',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_player1', to='opening_stats.players'),
        ),
        migrations.AlterField(
            model_name='matches',
            name='player2',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_player2', to='opening_stats.players'),
        ),
        migrations.AddIndex(
            model_name='advancedqueryqueue',
            index=models.Index(fields=['stale', 'query'], name='advanced_qu_stale_eeebf6_idx'),
        ),
    ]
