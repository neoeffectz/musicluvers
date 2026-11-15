from django.urls import path
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy

from . import views
from .accounts import QueuedPasswordResetForm

app_name = "users"

urlpatterns = [
    path("", views.home, name="home"),
    path("signup/", views.signup, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("welcome/about/", views.about_you, name="about_you"),
    path("welcome/about/photo/", views.about_you_photo, name="about_you_photo"),
    path("welcome/locate/", views.locate, name="locate"),
    path("safety/<uuid:user_id>/block/", views.block_view, name="block"),
    path("safety/<uuid:user_id>/report/", views.report_view, name="report"),

    # Password reset (emails only actually send once Resend + the domain are set up; see settings EMAIL_ENABLED)
    path("password/reset/", auth_views.PasswordResetView.as_view(
        form_class=QueuedPasswordResetForm,
        template_name="users/account/password_reset.html",
        email_template_name="users/emails/password_reset.txt",
        html_email_template_name="users/emails/password_reset.html",
        subject_template_name="users/emails/password_reset_subject.txt",
        success_url=reverse_lazy("users:password_reset_done"),
    ), name="password_reset"),
    path("password/reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="users/account/password_reset_done.html"), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="users/account/password_reset_confirm.html",
        success_url=reverse_lazy("users:password_reset_complete"),
    ), name="password_reset_confirm"),
    path("password/reset/complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="users/account/password_reset_complete.html"), name="password_reset_complete"),

    path("verify-email/resend/", views.resend_verification, name="resend_verification"),
    path("verify-email/<str:token>/", views.verify_email, name="verify_email"),
    path("account/delete/", views.delete_account, name="delete_account"),
    path("terms/", views.terms, name="terms"),
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
    path("push/test/", views.push_test, name="push_test"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("privacy/", views.privacy, name="privacy"),
    path("profile/tab/", views.profile_tab, name="profile_tab"),
    path("profile/update/", views.profile_update, name="profile_update"),
    path("profile/photo/<uuid:photo_id>/delete/", views.profile_photo_delete, name="profile_photo_delete"),
]