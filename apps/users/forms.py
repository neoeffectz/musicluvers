from datetime import date

from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password

from .models import CustomUser


class SignupForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, strip=False)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("email") and cleaned.get("password"):
            candidate = CustomUser(
                username=cleaned["email"], email=cleaned["email"], first_name=cleaned.get("first_name", ""),
            )
            try:
                validate_password(cleaned["password"], user=candidate)
            except forms.ValidationError as e:
                self.add_error("password", e)
        return cleaned

    def save(self):
        return CustomUser.objects.create_user(
            username=self.cleaned_data["email"],
            email=self.cleaned_data["email"],
            first_name=self.cleaned_data["first_name"].strip(),
            password=self.cleaned_data["password"],
        )


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, strip=False)

    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        self.user = authenticate(
            self.request, username=cleaned["email"].strip().lower(), password=cleaned["password"],
        )
        if self.user is None:
            raise forms.ValidationError("Wrong email or password.")
        return cleaned


MIN_AGE = 18
MAX_PHOTO_BYTES = 10 * 1024 * 1024


class AboutYouForm(forms.Form):
    gender = forms.ChoiceField(choices=CustomUser.GENDER_CHOICES)
    preference = forms.ChoiceField(choices=CustomUser.PREFERENCE_CHOICES)
    birthdate = forms.DateField()

    def clean_birthdate(self):
        birthdate = self.cleaned_data["birthdate"]
        today = date.today()
        age = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))
        if age < MIN_AGE:
            raise forms.ValidationError(f"You need to be {MIN_AGE} or older to use Music Luvers.")
        if age > 100:
            raise forms.ValidationError("Check your birthdate.")
        return birthdate


# Still photos only. We look at the file's first bytes (not the name or the type the browser claims),
# so a renamed video, or a moving GIF/WebP, is turned away.
HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"mif1", b"msf1", b"hevc", b"avif"}  # iPhone HEIC / AVIF
VIDEO_MESSAGE = "Videos can't be used. Pick a photo (JPG or PNG)."
NOT_PHOTO_MESSAGE = "That file isn't a photo. Pick a JPG or PNG."


def photo_problem(head):
    """Returns None for a still JPG/PNG/WebP photo, otherwise the message to show."""
    if head.startswith(b"\xff\xd8\xff"):  # JPEG
        return None
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return VIDEO_MESSAGE if b"acTL" in head[:200] else None  # acTL = animated PNG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        if head[12:16] == b"VP8X" and len(head) > 20 and head[20] & 0x02:  # animation flag
            return VIDEO_MESSAGE
        return None
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "Moving GIFs can't be used. Pick a photo (JPG or PNG)."
    if head[4:8] == b"ftyp":  # MP4 / MOV / HEIC all start like this; the brand tells them apart
        if head[8:12] in HEIF_BRANDS:
            # Most browsers can't show HEIC. Phones convert to JPG for us (the picker doesn't list HEIC).
            return "That's an iPhone HEIC photo, which most phones can't show. Pick it from your phone's photos, or use a JPG or PNG."
        return VIDEO_MESSAGE
    if head[:4] in (b"\x1aE\xdf\xa3", b"RIFF") or head[:3] == b"FLV":  # WebM/MKV, AVI, FLV
        return VIDEO_MESSAGE
    return NOT_PHOTO_MESSAGE


class PhotoForm(forms.Form):
    photo = forms.FileField()

    def clean_photo(self):
        photo = self.cleaned_data["photo"]
        if (photo.content_type or "").startswith("video/"):
            raise forms.ValidationError(VIDEO_MESSAGE)
        if photo.size > MAX_PHOTO_BYTES:
            raise forms.ValidationError("That photo is over 10 MB. Pick a smaller one.")
        photo.seek(0)
        head = photo.read(512)
        photo.seek(0)
        problem = photo_problem(head)
        if problem:
            raise forms.ValidationError(problem)
        return photo
