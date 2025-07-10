from typing import List, Dict, Any
from urllib.parse import urlparse
# ========== 3. PROFILE PREPROCESSING HELPERS ==========
def normalize_url(url):
    return url.strip().rstrip('/')

def summarize_skills(skills: List[Dict]) -> str:
    return ', '.join([s.get('title', '') for s in skills if s.get('title')])

def summarize_projects(projects: List[Dict]) -> str:
    summaries = []
    for p in projects:
        title = p.get('title', '')
        desc = ''
        if p.get('subComponents'):
            for comp in p['subComponents']:
                for d in comp.get('description', []):
                    if d.get('type') == 'textComponent':
                        desc += d.get('text', '') + ' '
        summaries.append(f"{title}: {desc.strip()}")
    return '\n'.join(summaries)

def summarize_educations(educations: List[Dict]) -> str:
    return ', '.join([
        f"{e.get('title', '')} ({e.get('subtitle', '')}, {e.get('caption', '')})"
        for e in educations if e.get('title')
    ])

def summarize_certs(certs: List[Dict]) -> str:
    return ', '.join([
        f"{c.get('title', '')} ({c.get('subtitle', '')}, {c.get('caption', '')})"
        for c in certs if c.get('title')
    ])

def summarize_test_scores(scores: List[Dict]) -> str:
    return ', '.join([
        f"{s.get('title', '')} ({s.get('subtitle', '')})"
        for s in scores if s.get('title')
    ])

def summarize_generic(items: List[Dict], key='title') -> str:
    return ', '.join([item.get(key, '') for item in items if item.get(key)])


# === Preprocess raw profile into summarized profile ===
def preprocess_profile(raw_profile: Dict[str, Any]) -> Dict[str, str]:
    return {
        "FullName": raw_profile.get("fullName", ""),
        "profile_url": raw_profile.get("linkedinUrl",""),
        "Headline": raw_profile.get("headline", ""),
        "JobTitle": raw_profile.get("jobTitle", ""),
        "CompanyName": raw_profile.get("companyName", ""),
        "CompanyIndustry": raw_profile.get("companyIndustry", ""),
        "CurrentJobDuration": str(raw_profile.get("currentJobDuration", "")),
        "About": raw_profile.get("about", ""),
        "Experiences": summarize_generic(raw_profile.get("experiences", []), key='title'),
        "Skills": summarize_skills(raw_profile.get("skills", [])),
        "Educations": summarize_educations(raw_profile.get("educations", [])),
        "Certifications": summarize_certs(raw_profile.get("licenseAndCertificates", [])),
        "HonorsAndAwards": summarize_generic(raw_profile.get("honorsAndAwards", []), key='title'),
        "Verifications": summarize_generic(raw_profile.get("verifications", []), key='title'),
        "Highlights": summarize_generic(raw_profile.get("highlights", []), key='title'),
        "Projects": summarize_projects(raw_profile.get("projects", [])),
        "Publications": summarize_generic(raw_profile.get("publications", []), key='title'),
        "Patents": summarize_generic(raw_profile.get("patents", []), key='title'),
        "Courses": summarize_generic(raw_profile.get("courses", []), key='title'),
        "TestScores": summarize_test_scores(raw_profile.get("testScores", []))
    }

# === Create & fill state ===


def initialize_state(raw_profile: Dict[str, Any]) -> Dict[str,Any]:
    """
    Initializes the chatbot state used in LangGraph:
    - Keeps both raw and processed profile
    - Splits important sections for quick access
    - Initializes placeholders for tool outputs
    - Adds empty chat history for conversation context
    """
    # Your preprocessing function that cleans / normalizes scraped profile
    profile = preprocess_profile(raw_profile)
    print(f"initializing url as {profile['profile_url']}")

    state: Dict[str, Any] = {
        "profile": profile,             # Cleaned & normalized profile
        "profile_url": normalize_url(profile.get("profile_url","") or ""),

        # === Separate sections (make sure all are strings, never None) ===
        "sections": {
            "about": profile.get("About", "") or "",
            "headline": profile.get("Headline", "") or "",
            "skills": profile.get("Skills", "") or "",
            "projects": profile.get("Projects", "") or "",
            "educations": profile.get("Educations", "") or "",
            "certifications": profile.get("Certifications", "") or "",
            "honors_and_awards": profile.get("HonorsAndAwards", "") or "",
            "experiences": profile.get("Experiences", "") or "",
            "publications": profile.get("Publications", "") or "",
            "patents": profile.get("Patents", "") or "",
            "courses": profile.get("Courses", "") or "",
            "test_scores": profile.get("TestScores", "") or "",
            "verifications": profile.get("Verifications", "") or "",
            "highlights": profile.get("Highlights", "") or "",
            "job_title": profile.get("JobTitle", "") or "",
            "company_name": profile.get("CompanyName", "") or "",
            "company_industry": profile.get("CompanyIndustry", "") or "",
            "current_job_duration": profile.get("CurrentJobDuration", "") or "",
            "full_name": profile.get("FullName", "") or ""
        },

        # === Placeholders populated by tools ===
        "enhanced_content": {},        # Populated by ContentGenerator tool
        "profile_analysis": None,      # Can be None initially (Optional)
        "job_fit": None,               # Can be None initially (Optional)
        "target_role": None,           # Optional[str]
        "editing_section": None,       # Optional[str]

        # === Chat history ===
        # Pydantic expects list of dicts like {"role": "user", "content": "..."}
        "messages": [],
        "next_tool_name": None
    }
    

    return state

