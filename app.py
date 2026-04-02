import json
import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import anthropic
import models

app = Flask(__name__)
app.secret_key = os.urandom(24)

models.init_db()

PIPELINE_STATUSES = [
    "new", "researching", "contacted", "replied", "meeting_booked",
    "sampling", "quoting", "negotiating", "won", "lost"
]
QUOTE_STATUSES = ["draft", "sent", "accepted", "rejected", "expired"]


def get_client():
    profile = models.get_profile()
    api_key = profile.get("anthropic_api_key", "")
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    return anthropic.Anthropic(api_key=api_key)


# ─── Dashboard ───
@app.route("/")
def dashboard():
    leads = models.get_leads()
    pipeline = models.get_pipeline_stats()
    quote_stats = models.get_quote_stats()
    return render_template(
        "dashboard.html",
        leads=leads,
        pipeline=pipeline,
        quote_stats=quote_stats,
        total_leads=len(leads),
        statuses=PIPELINE_STATUSES,
    )


# ─── Lead Finder ───
@app.route("/find-leads", methods=["GET", "POST"])
def find_leads():
    results = []
    error = None
    if request.method == "POST":
        client = get_client()
        if not client:
            error = "No Anthropic API key configured. Set it in Profile Settings."
        else:
            profile = models.get_profile()
            search_type = request.form.get("search_type", "both")
            ingredient_focus = request.form.get("ingredient_focus", "")
            num_leads = int(request.form.get("num_leads", 5))

            type_desc = {
                "brands": "UK supplement/nutraceutical brands that do their own manufacturing in-house and buy raw ingredients directly from suppliers",
                "manufacturers": "UK supplement/nutraceutical contract manufacturers (CMOs) that produce supplements for other brands and buy raw ingredients",
                "both": "Both: (1) UK supplement/nutraceutical brands that do their own manufacturing in-house and buy raw ingredients directly, AND (2) UK supplement/nutraceutical contract manufacturers (CMOs) that produce for other brands"
            }.get(search_type, search_type)

            supplier_info = ""
            if profile.get("company_name"):
                supplier_info += f"Supplier company: {profile['company_name']}. "
            if profile.get("ingredients_offered"):
                supplier_info += f"Ingredients offered: {profile['ingredients_offered']}. "
            if profile.get("tagline"):
                supplier_info += f"Value proposition: {profile['tagline']}. "

            prompt = f"""You are a B2B lead generation expert for the UK nutraceutical/supplement ingredient supply industry.

{supplier_info}

Find exactly {num_leads} real, specific companies that match this description:
{type_desc}

{"Focus on companies likely to need: " + ingredient_focus if ingredient_focus else ""}

For EACH company, provide this JSON structure (return a JSON array):
{{
  "company_name": "Exact company name",
  "company_type": "brand_manufacturer" or "contract_manufacturer",
  "website": "company website URL",
  "location": "UK city/region",
  "description": "What they do, products, manufacturing capabilities (2-3 sentences)",
  "fit_score": 0-100 score for how good a fit they are as a customer for a UK ingredient supplier,
  "fit_reasoning": "Why this score - consider size, ingredient needs, buying patterns",
  "contact_name": "Most relevant buyer/procurement contact name if findable",
  "contact_role": "Their job title",
  "contact_email": "Professional email if findable, otherwise best-guess format like firstname@company.com",
  "contact_linkedin": "LinkedIn profile URL if findable",
  "email_sequence": [
    {{
      "subject": "Email 1 subject - cold intro",
      "body": "Professional cold email body (3-4 paragraphs). Reference their specific products/needs. Mention our ingredients/capabilities."
    }},
    {{
      "subject": "Email 2 subject - value-add follow-up",
      "body": "Follow-up email with industry insight or case study angle (3-4 paragraphs)"
    }},
    {{
      "subject": "Email 3 subject - direct ask",
      "body": "Final follow-up with specific offer or meeting request (2-3 paragraphs)"
    }}
  ],
  "linkedin_messages": [
    {{
      "type": "connection_request",
      "message": "Short LinkedIn connection request note (under 300 chars)"
    }},
    {{
      "type": "followup_1",
      "message": "First LinkedIn message after connecting (2-3 paragraphs)"
    }},
    {{
      "type": "followup_2",
      "message": "Second LinkedIn follow-up message (1-2 paragraphs)"
    }}
  ]
}}

Return ONLY a valid JSON array. No markdown, no code fences, no explanation."""

            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=8000,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.content[0].text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1]
                    text = text.rsplit("```", 1)[0]
                data = json.loads(text)
                for lead_data in data:
                    lead_id = models.create_lead(**lead_data)
                    lead_data["id"] = lead_id
                    results.append(lead_data)
                flash(f"Found and saved {len(results)} leads!", "success")
            except json.JSONDecodeError:
                error = "Failed to parse AI response. Try again."
            except anthropic.APIError as e:
                error = f"API error: {str(e)}"

    return render_template("find_leads.html", results=results, error=error)


# ─── Enrich Companies ───
@app.route("/enrich", methods=["GET", "POST"])
def enrich():
    results = []
    error = None
    if request.method == "POST":
        client = get_client()
        if not client:
            error = "No Anthropic API key configured. Set it in Profile Settings."
        else:
            profile = models.get_profile()
            companies_text = request.form.get("companies", "").strip()
            if not companies_text:
                error = "Please paste at least one company name."
            else:
                company_list = [c.strip() for c in companies_text.split("\n") if c.strip()]
                supplier_info = ""
                if profile.get("company_name"):
                    supplier_info += f"Supplier: {profile['company_name']}. "
                if profile.get("ingredients_offered"):
                    supplier_info += f"Ingredients: {profile['ingredients_offered']}. "

                prompt = f"""You are a B2B research expert for the UK nutraceutical ingredient supply industry.

{supplier_info}

Research and enrich these companies:
{json.dumps(company_list)}

For EACH company, provide this JSON structure (return a JSON array):
{{
  "company_name": "Company name",
  "company_type": "brand_manufacturer" or "contract_manufacturer" or "unknown",
  "website": "website URL",
  "location": "Location",
  "description": "What they do (2-3 sentences)",
  "fit_score": 0-100,
  "fit_reasoning": "Why this score",
  "contact_name": "Key buyer/procurement contact",
  "contact_role": "Job title",
  "contact_email": "Email if findable",
  "contact_linkedin": "LinkedIn URL if findable",
  "email_sequence": [
    {{"subject": "Intro email subject", "body": "Cold email body tailored to this company"}},
    {{"subject": "Follow-up subject", "body": "Follow-up email body"}},
    {{"subject": "Final follow-up subject", "body": "Final email body"}}
  ],
  "linkedin_messages": [
    {{"type": "connection_request", "message": "Connection note under 300 chars"}},
    {{"type": "followup_1", "message": "First LinkedIn message"}},
    {{"type": "followup_2", "message": "Second LinkedIn follow-up"}}
  ]
}}

Return ONLY valid JSON array."""

                try:
                    resp = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=8000,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = resp.content[0].text.strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1]
                        text = text.rsplit("```", 1)[0]
                    data = json.loads(text)
                    for lead_data in data:
                        lead_id = models.create_lead(**lead_data)
                        lead_data["id"] = lead_id
                        results.append(lead_data)
                    flash(f"Enriched and saved {len(results)} companies!", "success")
                except json.JSONDecodeError:
                    error = "Failed to parse AI response. Try again."
                except anthropic.APIError as e:
                    error = f"API error: {str(e)}"

    return render_template("enrich.html", results=results, error=error)


# ─── Pipeline ───
@app.route("/pipeline")
def pipeline():
    leads = models.get_leads()
    grouped = {s: [] for s in PIPELINE_STATUSES}
    for lead in leads:
        status = lead.get("pipeline_status", "new")
        if status not in grouped:
            status = "new"
        grouped[status].append(lead)
    return render_template(
        "pipeline.html",
        grouped=grouped,
        statuses=PIPELINE_STATUSES,
        total=len(leads),
    )


@app.route("/api/lead/<int:lead_id>/status", methods=["POST"])
def update_lead_status(lead_id):
    new_status = request.json.get("status")
    if new_status in PIPELINE_STATUSES:
        models.update_lead(lead_id, pipeline_status=new_status)
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid status"}), 400


# ─── Lead Detail ───
@app.route("/lead/<int:lead_id>")
def lead_detail(lead_id):
    lead = models.get_lead(lead_id)
    if not lead:
        flash("Lead not found.", "error")
        return redirect(url_for("pipeline"))
    return render_template(
        "lead_detail.html", lead=lead, statuses=PIPELINE_STATUSES
    )


@app.route("/lead/<int:lead_id>/update", methods=["POST"])
def update_lead(lead_id):
    data = {
        "pipeline_status": request.form.get("pipeline_status", "new"),
        "notes": request.form.get("notes", ""),
        "contact_name": request.form.get("contact_name", ""),
        "contact_email": request.form.get("contact_email", ""),
        "contact_phone": request.form.get("contact_phone", ""),
        "contact_linkedin": request.form.get("contact_linkedin", ""),
        "contact_role": request.form.get("contact_role", ""),
    }
    models.update_lead(lead_id, **data)
    flash("Lead updated.", "success")
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/lead/<int:lead_id>/delete", methods=["POST"])
def delete_lead_route(lead_id):
    models.delete_lead(lead_id)
    flash("Lead deleted.", "success")
    return redirect(url_for("pipeline"))


# ─── Quotes CRM ───
@app.route("/quotes")
def quotes():
    all_quotes = models.get_quotes()
    stats = models.get_quote_stats()
    leads = models.get_leads()
    return render_template(
        "quotes.html",
        quotes=all_quotes,
        stats=stats,
        leads=leads,
        quote_statuses=QUOTE_STATUSES,
    )


@app.route("/quotes/add", methods=["POST"])
def add_quote():
    data = {
        "lead_id": int(request.form["lead_id"]) if request.form.get("lead_id") else None,
        "ingredient": request.form.get("ingredient", ""),
        "quantity_kg": float(request.form.get("quantity_kg", 0)),
        "price_per_kg": float(request.form.get("price_per_kg", 0)),
        "status": request.form.get("status", "draft"),
        "notes": request.form.get("notes", ""),
    }
    models.create_quote(**data)
    flash("Quote added.", "success")
    return redirect(url_for("quotes"))


@app.route("/quotes/<int:qid>/update", methods=["POST"])
def update_quote_route(qid):
    data = {
        "ingredient": request.form.get("ingredient", ""),
        "quantity_kg": float(request.form.get("quantity_kg", 0)),
        "price_per_kg": float(request.form.get("price_per_kg", 0)),
        "status": request.form.get("status", "draft"),
        "notes": request.form.get("notes", ""),
    }
    if request.form.get("lead_id"):
        data["lead_id"] = int(request.form["lead_id"])
    models.update_quote(qid, **data)
    flash("Quote updated.", "success")
    return redirect(url_for("quotes"))


@app.route("/quotes/<int:qid>/delete", methods=["POST"])
def delete_quote_route(qid):
    models.delete_quote(qid)
    flash("Quote deleted.", "success")
    return redirect(url_for("quotes"))


# ─── Profile Settings ───
@app.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        models.update_profile(
            company_name=request.form.get("company_name", ""),
            contact_name=request.form.get("contact_name", ""),
            email=request.form.get("email", ""),
            phone=request.form.get("phone", ""),
            website=request.form.get("website", ""),
            ingredients_offered=request.form.get("ingredients_offered", ""),
            tagline=request.form.get("tagline", ""),
            anthropic_api_key=request.form.get("anthropic_api_key", ""),
        )
        flash("Profile saved!", "success")
        return redirect(url_for("profile"))
    p = models.get_profile()
    return render_template("profile.html", profile=p)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
