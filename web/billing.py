"""
web/billing.py -- Stripe billing blueprint for Opaux.
"""

import logging

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

log = logging.getLogger(__name__)

billing_bp = Blueprint("billing", __name__)

# ---------------------------------------------------------------------------
# Stripe lazy loader
# ---------------------------------------------------------------------------

def _get_stripe():
    """Return the stripe module, or None if not installed / not enabled."""
    if not current_app.config.get("STRIPE_ENABLED"):
        return None
    try:
        import stripe
        stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
        return stripe
    except ImportError:
        log.warning("stripe package is not installed.")
        return None


# Price-ID → plan name mapping
def _price_to_plan(price_id: str) -> str:
    if price_id == current_app.config.get("STRIPE_PRICE_STARTER"):
        return "starter"
    if price_id == current_app.config.get("STRIPE_PRICE_PRO"):
        return "pro"
    return "free"


# ---------------------------------------------------------------------------
# Public pricing page
# ---------------------------------------------------------------------------

@billing_bp.route("/pricing")
def pricing():
    return render_template("pricing.html")


# ---------------------------------------------------------------------------
# Create Checkout session
# ---------------------------------------------------------------------------

@billing_bp.route("/billing/create-checkout-session", methods=["POST"])
@login_required
def create_checkout_session():
    stripe = _get_stripe()
    if not stripe:
        flash("Billing is not configured.", "error")
        return redirect(url_for("billing.billing_status"))

    plan = request.form.get("plan", "starter")
    if plan == "pro":
        price_id = current_app.config.get("STRIPE_PRICE_PRO", "")
    else:
        price_id = current_app.config.get("STRIPE_PRICE_STARTER", "")

    if not price_id:
        flash("Selected plan is not available.", "error")
        return redirect(url_for("billing.billing_status"))

    # Create or retrieve Stripe customer
    customer_id = current_user.stripe_customer_id
    if not customer_id:
        try:
            customer = stripe.Customer.create(email=current_user.email)
            customer_id = customer.id
        except stripe.error.StripeError as exc:
            log.error("Stripe customer creation failed: %s", exc)
            flash("Could not create billing account. Please try again.", "error")
            return redirect(url_for("billing.billing_status"))

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=url_for("billing.billing_success", _external=True),
            cancel_url=url_for("billing.billing_status", _external=True),
            metadata={"user_id": current_user.id},
        )
    except stripe.error.StripeError as exc:
        log.error("Stripe checkout session creation failed: %s", exc)
        flash("Could not start checkout. Please try again.", "error")
        return redirect(url_for("billing.billing_status"))

    return redirect(session.url, code=303)


# ---------------------------------------------------------------------------
# Success / Cancel
# ---------------------------------------------------------------------------

@billing_bp.route("/billing/success")
@login_required
def billing_success():
    return render_template("billing.html", success=True)


@billing_bp.route("/billing/cancel")
def billing_cancel():
    return redirect(url_for("billing.billing_status"))


# ---------------------------------------------------------------------------
# Stripe webhook
# ---------------------------------------------------------------------------

@billing_bp.route("/billing/webhook", methods=["POST"])
def stripe_webhook():
    stripe = _get_stripe()
    if not stripe:
        return "Billing not configured", 400

    payload = request.get_data(as_text=False)
    sig_header = request.headers.get("Stripe-Signature", "")
    webhook_secret = current_app.config.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        log.warning("Stripe webhook signature verification failed.")
        return "Invalid signature", 400
    except Exception as exc:
        log.error("Stripe webhook error: %s", exc)
        return "Webhook error", 400

    _handle_webhook_event(event)
    return "", 200


def _handle_webhook_event(event) -> None:
    from web.auth import User

    event_type = event.get("type")
    data_object = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            user_id = data_object.get("metadata", {}).get("user_id")
            if not user_id:
                return
            customer_id = data_object.get("customer")
            subscription_id = data_object.get("subscription")
            # Derive plan from subscription price
            plan = "starter"
            if subscription_id:
                try:
                    import stripe as _stripe
                    _stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
                    sub = _stripe.Subscription.retrieve(subscription_id)
                    price_id = sub["items"]["data"][0]["price"]["id"]
                    plan = _price_to_plan(price_id)
                except Exception as exc:
                    log.error("Could not retrieve subscription plan: %s", exc)
            User.update_plan(user_id, plan, customer_id, subscription_id)
            log.info("User %s upgraded to plan=%s", user_id, plan)

        elif event_type == "customer.subscription.updated":
            subscription_id = data_object.get("id")
            customer_id = data_object.get("customer")
            status = data_object.get("status")
            price_id = data_object.get("items", {}).get("data", [{}])[0].get("price", {}).get("id", "")
            if status in ("active", "trialing"):
                plan = _price_to_plan(price_id)
            else:
                plan = "free"
            # Find user by customer ID
            user = _user_by_customer(customer_id)
            if user:
                User.update_plan(user.id, plan, customer_id, subscription_id)
                log.info("User %s subscription updated: plan=%s status=%s", user.id, plan, status)

        elif event_type == "customer.subscription.deleted":
            customer_id = data_object.get("customer")
            user = _user_by_customer(customer_id)
            if user:
                User.update_plan(user.id, "free", customer_id, None)
                log.info("User %s subscription deleted, reverted to free", user.id)

    except Exception as exc:
        log.error("Error handling webhook event %s: %s", event_type, exc)


def _user_by_customer(customer_id: str):
    """Find a user by their Stripe customer ID."""
    from web.auth import User
    for user in User.all_users():
        if user.stripe_customer_id == customer_id:
            return user
    return None


# ---------------------------------------------------------------------------
# Billing portal
# ---------------------------------------------------------------------------

@billing_bp.route("/billing/portal")
@login_required
def billing_portal():
    stripe = _get_stripe()
    if not stripe:
        flash("Billing is not configured.", "error")
        return redirect(url_for("billing.billing_status"))

    if not current_user.stripe_customer_id:
        flash("No billing account found.", "error")
        return redirect(url_for("billing.billing_status"))

    try:
        session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id,
            return_url=url_for("billing.billing_status", _external=True),
        )
    except stripe.error.StripeError as exc:
        log.error("Stripe portal session creation failed: %s", exc)
        flash("Could not open billing portal. Please try again.", "error")
        return redirect(url_for("billing.billing_status"))

    return redirect(session.url, code=303)


# ---------------------------------------------------------------------------
# Billing status page
# ---------------------------------------------------------------------------

@billing_bp.route("/billing")
@login_required
def billing_status():
    from web.config import Config

    stripe_enabled = current_app.config.get("STRIPE_ENABLED", False)
    plan = current_user.plan
    limit = Config.PLAN_LIMITS.get(plan, 20)
    usage = current_user.api_calls_this_month
    remaining = max(0, limit - usage)

    return render_template(
        "billing.html",
        success=False,
        stripe_enabled=stripe_enabled,
        plan=plan,
        usage=usage,
        limit=limit,
        remaining=remaining,
    )
