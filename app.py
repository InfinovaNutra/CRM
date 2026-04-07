import json
import re
import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
import anthropic
import models


def extract_json(text):
    """Extract JSON array from AI response, handling markdown fences and extra text."""
    text = text.strip()
    # Remove markdown code fences
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    text = text.strip()
    # Try parsing directly
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find JSON array in the text
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("No valid JSON array found", text, 0)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nutraleads-dev-key-change-in-prod")

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

            existing_leads = models.get_leads()
            existing_names = [l["company_name"] for l in existing_leads]
            exclude_text = ""
            if existing_names:
                exclude_text = f"\n\nIMPORTANT: Do NOT include any of these companies (already in our database):\n{json.dumps(existing_names)}\nFind completely different companies not in this list.\n"

            prompt = f"""You are a B2B lead generation expert for the UK nutraceutical/supplement ingredient supply industry.

{supplier_info}

Find exactly {num_leads} real, specific companies that match this description:
{type_desc}

{"Focus on companies likely to need: " + ingredient_focus if ingredient_focus else ""}
{exclude_text}

For EACH company, provide this JSON structure (return a JSON array):
{{
  "company_name": "Exact company name",
  "company_type": "brand_manufacturer" or "contract_manufacturer",
  "website": "company website URL",
  "location": "UK city/region",
  "description": "What they do, products, manufacturing capabilities (2-3 sentences)",
  "fit_score": 0-100 score for how good a fit they are as a customer for a UK ingredient supplier,
  "fit_reasoning": "Why this score - consider size, ingredient needs, buying patterns"
}}

Do NOT include contact details, emails, or outreach messages. Only company information.

Return ONLY a valid JSON array. No markdown, no code fences, no explanation."""

            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=8000,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = resp.content[0].text
                data = extract_json(text)
                existing_lower = {n.lower() for n in existing_names}
                skipped = 0
                for lead_data in data:
                    if lead_data.get("company_name", "").lower() in existing_lower:
                        skipped += 1
                        continue
                    lead_id = models.create_lead(**lead_data)
                    lead_data["id"] = lead_id
                    results.append(lead_data)
                    existing_lower.add(lead_data["company_name"].lower())
                msg = f"Found and saved {len(results)} new leads!"
                if skipped:
                    msg += f" ({skipped} duplicates skipped)"
                flash(msg, "success")
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
                existing_leads = models.get_leads()
                existing_lower = {l["company_name"].lower() for l in existing_leads}
                # Filter out companies already in database
                new_companies = [c for c in company_list if c.lower() not in existing_lower]
                already_exist = len(company_list) - len(new_companies)
                if not new_companies:
                    error = "All those companies are already in your database."
                    return render_template("enrich.html", results=results, error=error)
                company_list = new_companies
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
  "fit_reasoning": "Why this score"
}}

Do NOT include contact details, emails, or outreach messages. Only company information.

Return ONLY valid JSON array."""

                try:
                    resp = client.messages.create(
                        model="claude-sonnet-4-20250514",
                        max_tokens=8000,
                        messages=[{"role": "user", "content": prompt}],
                    )
                    text = resp.content[0].text
                    data = extract_json(text)
                    for lead_data in data:
                        lead_id = models.create_lead(**lead_data)
                        lead_data["id"] = lead_id
                        results.append(lead_data)
                    msg = f"Enriched and saved {len(results)} companies!"
                    if already_exist:
                        msg += f" ({already_exist} already in database, skipped)"
                    flash(msg, "success")
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


# ─── Generate Outreach ───
@app.route("/lead/<int:lead_id>/generate-outreach", methods=["POST"])
def generate_outreach(lead_id):
    lead = models.get_lead(lead_id)
    if not lead:
        flash("Lead not found.", "error")
        return redirect(url_for("pipeline"))
    if not lead.get("contact_name"):
        flash("Please add a contact name before generating outreach.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id))
    client = get_client()
    if not client:
        flash("No Anthropic API key configured. Set it in Profile Settings.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id))

    profile = models.get_profile()
    supplier_info = ""
    if profile.get("company_name"):
        supplier_info += f"Your company: {profile['company_name']}. "
    if profile.get("contact_name"):
        supplier_info += f"Your name: {profile['contact_name']}. "
    if profile.get("ingredients_offered"):
        supplier_info += f"Ingredients you supply: {profile['ingredients_offered']}. "
    if profile.get("tagline"):
        supplier_info += f"Value proposition: {profile['tagline']}. "

    prompt = f"""You are a B2B sales copywriter for the UK nutraceutical ingredient supply industry.

{supplier_info}

Write personalised outreach for this lead:
- Company: {lead['company_name']}
- Type: {lead.get('company_type', 'unknown')}
- Description: {lead.get('description', 'N/A')}
- Location: {lead.get('location', 'UK')}
- Contact person: {lead['contact_name']}
- Contact role: {lead.get('contact_role', 'N/A')}

Generate a JSON object with:
{{
  "email_sequence": [
    {{
      "subject": "Email 1 subject - personalised cold intro",
      "body": "Professional cold email (3-4 paragraphs). Address {lead['contact_name']} by name. Reference {lead['company_name']}'s specific products/needs. Mention our ingredients/capabilities. Include a clear CTA."
    }},
    {{
      "subject": "Email 2 subject - value-add follow-up",
      "body": "Follow-up email (3-4 paragraphs) with industry insight or relevant case study angle. Reference the first email."
    }},
    {{
      "subject": "Email 3 subject - direct ask",
      "body": "Final follow-up (2-3 paragraphs) with specific offer or meeting request. Create urgency."
    }}
  ],
  "linkedin_messages": [
    {{
      "type": "connection_request",
      "message": "Short personalised LinkedIn connection request to {lead['contact_name']} (under 300 chars). Mention {lead['company_name']} specifically."
    }},
    {{
      "type": "followup_1",
      "message": "First LinkedIn message after connecting (2-3 paragraphs). Reference their company and role."
    }},
    {{
      "type": "followup_2",
      "message": "Second LinkedIn follow-up (1-2 paragraphs). Direct ask for a call or meeting."
    }}
  ]
}}

Make all messages sound natural and human, not templated. Use UK English.
Return ONLY valid JSON. No markdown, no code fences."""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        # extract_json returns a list, but here we expect an object
        text = text.strip()
        text = re.sub(r'^```(?:json)?\s*\n?', '', text)
        text = re.sub(r'\n?```\s*$', '', text)
        text = text.strip()
        try:
            outreach = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                outreach = json.loads(match.group())
            else:
                raise
        models.update_lead(
            lead_id,
            email_sequence=outreach.get("email_sequence", []),
            linkedin_messages=outreach.get("linkedin_messages", []),
        )
        flash(f"Outreach generated for {lead['contact_name']} at {lead['company_name']}!", "success")
    except json.JSONDecodeError:
        flash("Failed to parse AI response. Try again.", "error")
    except anthropic.APIError as e:
        flash(f"API error: {str(e)}", "error")

    return redirect(url_for("lead_detail", lead_id=lead_id))


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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
