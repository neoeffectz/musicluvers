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


class PhotoForm(forms.Form):
    photo = forms.FileField()

    def clean_photo(self):
        photo = self.cleaned_data["photo"]
        if not (photo.content_type or "").startswith("image/"):
            raise forms.ValidationError("That file isn't a photo. Pick a JPG or PNG.")
        if photo.size > MAX_PHOTO_BYTES:
            raise forms.ValidationError("That photo is over 10 MB. Pick a smaller one.")
        return photo
