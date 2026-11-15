from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("spotify_match", "0004_notification"),
    ]

    operations = [
        migrations.AlterUniqueTogether(name="topartist", unique_together=set()),
        migrations.AlterUniqueTogether(name="toptrack", unique_together=set()),
        migrations.RenameField(model_name="topartist", old_name="spotify_artist_id", new_name="external_id"),
        migrations.RenameField(model_name="toptrack", old_name="spotify_track_id", new_name="external_id"),
        migrations.AddField(
            model_name="topartist",
            name="source",
            field=models.CharField(
                choices=[("manual", "Picked by hand"), ("spotify", "Spotify"), ("lastfm", "Last.fm")],
                default="spotify", max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="toptrack",
            name="source",
            field=models.CharField(
                choices=[("manual", "Picked by hand"), ("spotify", "Spotify"), ("lastfm", "Last.fm")],
                default="spotify", max_length=16,
            ),
        ),
        migrations.AlterUniqueTogether(name="topartist", unique_together={("user", "source", "external_id")}),
        migrations.AlterUniqueTogether(name="toptrack", unique_together={("user", "source", "external_id")}),
    ]
