import os
import json
import re
import time
from typing import Dict, Any, List, Optional, Annotated
from chatbot_model import (
    UserMemory,
    ChatbotState,
    ProfileAnalysisModel,
    JobFitModel,
    ContentGenerationModel,
    
)
from llm_utils import call_llm_and_parse
from profile_preprocessing import (
    preprocess_profile,
    initialize_state,
    normalize_url
)
from openai import OpenAI
import streamlit as st
import hashlib
from dotenv import load_dotenv
from pydantic import BaseModel, Field,ValidationError
# import pdb; pdb.set_trace()
from scraping_profile import scrape_linkedin_profile
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage,BaseMessage,ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END,START
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import add_messages  # if your framework exposes this
from langgraph.prebuilt import ToolNode,tools_condition,InjectedState
import dirtyjson
import sqlite3
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    SQLITE_AVAILABLE = True
except ImportError:
    SQLITE_AVAILABLE = False



# ========== 1. ENVIRONMENT & LLM SETUP ==========
load_dotenv()
groq_key = os.getenv("GROQ_API_KEY")
assert groq_key, "GROQ_API_KEY not found in environment!"
groq_client=OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )

def normalize_url(url):
    return url.strip().rstrip('/')

def validate_state(state: dict) -> None:
    """
    Validate given state dict against ChatbotState schema.
    Displays result in Streamlit instead of printing.
    """
    # st.write("=== Validating chatbot state ===")
    try:
        ChatbotState.model_validate(state)
        # st.success("✅ State is valid!")
    except ValidationError as e:
        st.error("❌ Validation failed!")
        errors_list = []
        for error in e.errors():
            loc = " → ".join(str(item) for item in error['loc'])
            msg = error['msg']
            errors_list.append(f"- At: {loc}\n  Error: {msg}")
        st.write("\n".join(errors_list))
        # Optionally show raw validation error too:
        st.expander("See raw validation error").write(str(e))
        st.stop()


user_memory = UserMemory()

# ========== 7. AGENT FUNCTIONS ==========

def profile_analysis_prompt(profile: Dict[str, str]) -> str:
    return f"""
You are a top-tier LinkedIn career coach and AI analyst.

Analyze the following candidate profile carefully.

Candidate profile data:
FullName: {profile.get("FullName", "")}
Headline: {profile.get("Headline", "")}
JobTitle: {profile.get("JobTitle", "")}
CompanyName: {profile.get("CompanyName", "")}
CompanyIndustry: {profile.get("CompanyIndustry", "")}
CurrentJobDuration: {profile.get("CurrentJobDuration", "")}
About: {profile.get("About", "")}
Experiences: {profile.get("Experiences", "")}
Skills: {profile.get("Skills", "")}
Educations: {profile.get("Educations", "")}
Certifications: {profile.get("Certifications", "")}
HonorsAndAwards: {profile.get("HonorsAndAwards", "")}
Verifications: {profile.get("Verifications", "")}
Highlights: {profile.get("Highlights", "")}
Projects: {profile.get("Projects", "")}
Publications: {profile.get("Publications", "")}
Patents: {profile.get("Patents", "")}
Courses: {profile.get("Courses", "")}
TestScores: {profile.get("TestScores", "")}


Identify and summarize:
1. strengths:
    - technical strengths (skills, tools, frameworks)
    - project strengths (impactful projects, innovation)
    - educational strengths (degrees, certifications, awards)
    - soft skills and personality traits (teamwork, leadership)
2. weaknesses:
    - missing or weak technical skills
    - gaps in projects, experience, or education
    - unclear profile sections or missing context
3. actionable suggestions:
    - concrete ways to improve profile headline, about section, or add projects
    - suggestions to learn or highlight new skills
    - ideas to make the profile more attractive for recruiters

Important instructions:
- Respond ONLY with valid JSON.
- Do NOT include text before or after JSON.
- Be concise but detailed.



Example JSON format:
{{
  "strengths": {{
    "technical": ["...", "..."],
    "projects": ["...", "..."],
    "education": ["...", "..."],
    "soft_skills": ["...", "..."]
  }},
  "weaknesses": {{
    "technical_gaps": ["...", "..."],
    "project_or_experience_gaps": ["...", "..."],
    "missing_context": ["...", "..."]
  }},
  "suggestions": [
    "...",
    "...",
    "..."
  ]
}}
""".strip()




def job_fit_prompt(sections: Dict[str, str], target_role: str) -> str:
    return f"""
You are an expert career coach and recruiter.

Compare the following candidate profile against the typical requirements for the role of "{target_role}".

Candidate Profile:
- Headline: {sections.get('headline', '')}
- About: {sections.get('about', '')}
- Job Title: {sections.get('job_title', '')}
- Company: {sections.get('company_name', '')}
- Industry: {sections.get('company_industry', '')}
- Current Job Duration: {sections.get('current_job_duration', '')}
- Skills: {sections.get('skills', '')}
- Projects: {sections.get('projects', '')}
- Educations: {sections.get('educations', '')}
- Certifications: {sections.get('certifications', '')}
- Honors & Awards: {sections.get('honors_and_awards', '')}
- Experiences: {sections.get('experiences', '')}

**Instructions:**
- Respond ONLY with valid JSON.
- Your JSON must exactly match the following schema:
{{
  "match_score": 85,
  "missing_skills": ["Skill1", "Skill2"],
  "suggestions": ["...", "...", "..."]
}}
- "match_score": integer from 0–100 estimating how well the profile fits the target role.
- "missing_skills": key missing or weakly mentioned skills.
- "suggestions": 3 actionable recommendations to improve fit (e.g., learn tools, rewrite headline).

Do NOT include explanations, text outside JSON, or markdown.
Start with '{{' and end with '}}'.
The JSON must be directly parseable.
""".strip()


# --- Tool: Profile Analyzer ---
@tool
def profile_analyzer(state: Annotated[ChatbotState, InjectedState]) -> dict:
    """
    Tool: Analyze the overall full user's profile to give strengths, weaknesses, suggestions.
    This is needed only if full analysis of profile is needed. 
    Returns the full analysis in the form of a json.

    - It takes no arguments
    """


    # Get summarized profile (dictionary of strings)
    profile = getattr(state, "profile", {}) or {}

    # Build prompt
    prompt = profile_analysis_prompt(profile)

    # Call the LLM & parse structured result
    analysis_model = call_llm_and_parse(groq_client,prompt, ProfileAnalysisModel)
    analysis_dict = analysis_model.model_dump()

    # Save to state and user memory
    state.profile_analysis = analysis_dict
    user_memory.save("profile_analysis", analysis_dict)

    print("💾 [DEBUG] Saved analysis to user memory.")
    print("📦 [DEBUG] Updated state.profile_analysis with analysis.")

    return analysis_dict

# --- Tool: Job Matcher ---


@tool
def job_matcher(
    state: Annotated[ChatbotState, InjectedState],
    target_role: str = None
) -> dict:
    """
    Tool: Analyze how well the user's profile fits the target role.
    - If user is asking if he is a good fit for a certain role, or needs to see if his profile is compatible with a certain role, call this.
    - Takes target_role as an argument.
    - this tool is needed when match score, missing skills, suggestions are needed based on a job name given.
    """
    print(f"target role is {target_role}")
    # Update state.target_role if provided

    sections = getattr(state, "sections", {})

    # Build prompt
    prompt = job_fit_prompt(sections, target_role)

    # Call LLM and parse
    try:
        job_fit_model = call_llm_and_parse(groq_client,prompt, JobFitModel)
        job_fit_dict = job_fit_model.model_dump()
        job_fit_dict["target_role"] = target_role
    except Exception as e:
        job_fit_dict = {
            "target_role":target_role,
            "match_score": 0,
            "missing_skills": [],
            "suggestions": ["Parsing failed or incomplete response."]
        }

    # Save to state and user memory
    state.job_fit = job_fit_dict
    user_memory.save("job_fit", job_fit_dict)

    return job_fit_dict






@tool
def extract_from_state_tool(
    state: Annotated[ChatbotState, InjectedState],
    key: str
) -> dict:
    """
    This tool is used if user wants to ask about any particular part of this profile. Use this if a singe section is targeted. It expects key as an arguement, that represents what
    the user is wanting to look at, from his profile.
    Argument:
      key: only pass one from the below list, identify one thing the user wants to look into and choose that:
        "sections.about", "sections.headline", "sections.skills", "sections.projects",
        "sections.educations", "sections.certifications", "sections.honors_and_awards",
        "sections.experiences", "sections.publications", "sections.patents",
        "sections.courses", "sections.test_scores", "sections.verifications",
        "sections.highlights", "sections.job_title", "sections.company_name",
        "sections.company_industry", "sections.current_job_duration", "sections.full_name",
        "enhanced_content,"profile_analysis", "job_fit", "target_role", "editing_section"
    """
    value = state
    try:
        for part in key.split('.'):
            # Support both dict and Pydantic model
            if isinstance(value, dict):
                value = value.get(part)
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                value = None
            if value is None:
                break
    except Exception:
        value = None
    return {"result": value}


tools = [
    profile_analyzer,
   job_matcher,
    extract_from_state_tool
]
llm = ChatOpenAI(
    api_key=groq_key,
    base_url="https://api.groq.com/openai/v1",
    model="llama3-8b-8192",
    temperature=0
)
llm_with_tools = llm.bind_tools(tools)



# ========== 8. LANGGRAPH PIPELINE ==========


def chatbot_node(state: ChatbotState) -> ChatbotState:
    validate_state(state)

    messages = state.get("messages", [])

    system_prompt = """
You are a helpful AI assistant specialized in LinkedIn profile coaching.

Guidelines:
- Greet the user if they greet you, and explain you can help analyze, enhance, and improve their LinkedIn profile.
- Prefer using tools instead of answering directly whenever this can give better, data-backed answers.
- Call only one tool at a time. Never call multiple tools together.

When to use tools:
- If the user asks to show a section (like About, Projects, etc.): call extract_from_state_tool, unless you already have that section stored.
- If the user asks to enhance a section: use extract_from_state_tool first if you don’t already have that section, then enhance it.
- If the user requests a full profile analysis: use profile_analyzer.
- If the user wants to know how well they fit a target job role: use job_matcher with the given role.
- Use tools to check strengths, weaknesses, missing skills, or improvement suggestions.
- If the tool was just called recently and info is still fresh, you may answer directly.

Important:
- Never describe or print JSON of a tool call.
- Never say "I'm about to call a tool" — just call the tool properly.
- Keep answers clear, helpful, and actionable.

Your goal: help the user see, improve, and analyze their LinkedIn profile.

"""
    recent_messages = []
    for msg in messages[-6:]:  # last few, e.g., 6
        if isinstance(msg, HumanMessage):
            recent_messages.append({
                "role": "user",
                "content": f"User asked: {msg.content}"
            })
        elif isinstance(msg, AIMessage):
        # keep only non-empty AI replies (actual answers)
            if msg.content.strip():
                recent_messages.append({
                    "role": "assistant",
                    "content": msg.content
                })
        elif isinstance(msg, ToolMessage):
            recent_messages.append({
                "role": "assistant",
                "content": f"[Tool: {msg.name}] {msg.content}"
            })


    # Build messages & invoke LLM
    messages = [SystemMessage(content=system_prompt)] + recent_messages
    # messages = [SystemMessage(content=system_prompt)]
    response = llm_with_tools.invoke(messages)
    if hasattr(response, "tool_calls") and response.tool_calls:
        first_tool = response.tool_calls[0]
        tool_name = first_tool.get("name") if isinstance(first_tool, dict) else getattr(first_tool, "name", None)
        tool_args = first_tool.get("args") if isinstance(first_tool, dict) else getattr(first_tool, "args", {})
        print(f"[DEBBBBUUUUGGG] using tool {tool_name}")

    # DEBUG
    print("[DEBUG] LLM response:", response)
    state.setdefault("messages", []).append(response)

    return state





# --- Graph definition ---
graph = StateGraph(state_schema=ChatbotState)
graph.add_node("chatbot", chatbot_node)
graph.add_node("tools", ToolNode(tools))
graph.add_edge(START, "chatbot")
graph.add_conditional_edges("chatbot", tools_condition)
graph.add_edge("tools","chatbot")
graph.set_entry_point("chatbot")

# --- Streamlit UI ---
st.set_page_config(page_title="💼 LinkedIn AI Career Assistant", page_icon="🤖", layout="wide")
st.title("🧑‍💼 LinkedIn AI Career Assistant")

# --- Checkpointer and graph initialization ---
if "checkpointer" not in st.session_state:
    if SQLITE_AVAILABLE:
        conn = sqlite3.connect("checkpoints1.db", check_same_thread=False)
        st.session_state["checkpointer"] = SqliteSaver(conn)
    else:
        st.session_state["checkpointer"] = MemorySaver()
checkpointer = st.session_state["checkpointer"]

if "app_graph" not in st.session_state:
    st.session_state["app_graph"] = graph.compile(checkpointer=checkpointer)
app_graph = st.session_state["app_graph"]
# Find or create thread
def find_thread_id_for_url(checkpointer, url, max_threads=100):
    search_url = normalize_url(url)
    for tid in range(max_threads):
        config = {"configurable": {"thread_id": str(tid), "checkpoint_ns": ""}}
        state = checkpointer.get(config)
        if state and "channel_values" in state:
            user_state = state["channel_values"]
            stored_url = normalize_url(user_state.get("profile_url", ""))
            if stored_url == search_url:
                return str(tid), user_state
    return None, None

def delete_thread_checkpoint(checkpointer, thread_id):
    # For SqliteSaver, use the delete_thread method if available
    if hasattr(checkpointer, "delete_thread"):
        checkpointer.delete_thread(thread_id)
    else:
        # For in-memory or custom checkpointers, implement as needed
        pass


def get_next_thread_id(checkpointer, max_threads=100):
    used = set()
    for tid in range(max_threads):
        config = {"configurable": {"thread_id": str(tid), "checkpoint_ns": ""}}
        if checkpointer.get(config):
            used.add(tid)
    for tid in range(max_threads):
        if tid not in used:
            return str(tid)
    raise RuntimeError("No available thread_id")

# --- Session selection and state initialization ---

if "chat_mode" not in st.session_state:
    profile_url = st.text_input("Profile URL (e.g., https://www.linkedin.com/in/username/)")
    if not profile_url:
        st.info("Please enter a valid LinkedIn profile URL above to start.")
        st.stop()

    valid_pattern = r"^https://www\.linkedin\.com/in/[^/]+/?$"
    if not re.match(valid_pattern, profile_url.strip()):
        st.error("❌ Invalid LinkedIn profile URL. Make sure it matches the format.")
        st.stop()
    url = profile_url.strip()

    existing_thread_id, previous_state = find_thread_id_for_url(checkpointer, url)
    # Defensive: ensure required fields
    required_fields = ["profile", "sections"]
    if previous_state and not all(f in previous_state and previous_state[f] for f in required_fields):
        st.warning("Previous session is missing required data. Please start a new chat.")
        previous_state = None

    if previous_state:
        st.info("A previous session found. Choose:")
        col1, col2 = st.columns(2)
        if col1.button("Continue previous chat"):
            st.session_state["chat_mode"] = "continue"
            st.session_state["thread_id"] = existing_thread_id
            st.session_state.state = previous_state
            st.rerun()
        elif col2.button("Start new chat"):
            delete_thread_checkpoint(checkpointer, existing_thread_id)
            with st.spinner("Fetching and processing profile... ⏳"):
                raw=scrape_linkedin_profile(url)
            thread_id = existing_thread_id
            st.session_state["chat_mode"] = "new"
            st.session_state["thread_id"] = thread_id
            st.session_state.state = initialize_state(raw)
            st.session_state.state["profile_url"] = normalize_url(url)
            st.session_state.state["messages"] = []
            st.rerun()
        st.stop()
    else:
        with st.spinner("Fetching and processing profile... ⏳"):
                raw=scrape_linkedin_profile(url)
        thread_id = get_next_thread_id(checkpointer)
        st.session_state["thread_id"] = thread_id
        st.session_state["chat_mode"] = "new"
        st.session_state.state = initialize_state(raw)
        st.session_state.state["profile_url"] = normalize_url(url)
        st.session_state.state["messages"] = []
        st.rerun()

# --- Main chat UI (only after chat_mode is set) ---
state = st.session_state.state
thread_id = st.session_state.get("thread_id")

st.subheader("💬 Chat with your AI Assistant")
messages = state.get("messages", [])
chat_container = st.container()

with chat_container:
    st.markdown(
        """
        <style>
        .chat-row { display: flex; width: 100%; margin-bottom: 12px; animation: fadeIn 0.5s; }
        .chat-row.user { justify-content: flex-end; }
        .chat-row.ai { justify-content: flex-start; }
        .chat-bubble { font-family: 'Segoe UI', 'Roboto', 'Arial', sans-serif; font-size: 1.08rem; line-height: 1.65; padding: 14px 22px; border-radius: 20px; min-width: 60px; max-width: 75vw; box-shadow: 0 2px 12px rgba(0,0,0,0.10); word-break: break-word; display: inline-block; position: relative; margin-bottom: 2px; }
        .bubble-user { background: linear-gradient(90deg, #43e97b 0%, #38f9d7 100%); color: #fff; border-bottom-right-radius: 6px; border-top-right-radius: 22px; text-align: right; box-shadow: 0 4px 16px rgba(67,233,123,0.13); }
        .bubble-ai { background: linear-gradient(90deg, #e3f0ff 0%, #c9eaff 100%); color: #1a237e; border-bottom-left-radius: 6px; border-top-left-radius: 22px; text-align: left; border: 1.5px solid #b3e0fc; box-shadow: 0 4px 16px rgba(44, 62, 80, 0.08); }
        .bubble-unknown { background: #fffbe6; color: #8a6d3b; border-radius: 14px; text-align: center; border: 1px solid #ffe082; display: inline-block; }
        .sender-label { font-size: 0.93em; font-weight: 600; opacity: 0.7; margin-bottom: 4px; display: block; }
        .avatar { width: 38px; height: 38px; border-radius: 50%; margin-right: 10px; margin-top: 2px; background: #e0e0e0; object-fit: cover; box-shadow: 0 2px 6px rgba(0,0,0,0.07); }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(12px);} to { opacity: 1; transform: translateY(0);} }
        </style>
        """,
        unsafe_allow_html=True,
    )

    job_fit = state.get("job_fit")
    for msg in messages:
        if isinstance(msg, HumanMessage):
            st.markdown(
                f"""
                <div class="chat-row user">
                    <div class="chat-bubble bubble-user">
                        <span class="sender-label">🧑‍💻 You</span>
                        {msg.content}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif isinstance(msg, AIMessage):
            if not msg.content or not msg.content.strip():
                continue
            st.markdown(
                f"""
                <div class="chat-row ai">
                    <img class="avatar" src="https://img.icons8.com/ios-filled/50/1a237e/robot-2.png" alt="AI"/>
                    <div class="chat-bubble bubble-ai">
                        <span class="sender-label">🤖 AI</span>
                        {msg.content}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        elif isinstance(msg, ToolMessage):
            raw_content = msg.content or "(no content)"
            try:
                parsed = json.loads(raw_content)
            except Exception:
                parsed = None

            if parsed and isinstance(parsed, dict):
                # --- Profile analysis format ---
                if all(k in parsed for k in ("strengths", "weaknesses", "suggestions")):
                    strengths = parsed["strengths"]
                    weaknesses = parsed["weaknesses"]
                    suggestions = parsed["suggestions"]
                    formatted_html = f"""
                    <h3>💪 <b>Strengths</b></h3>
                    <ul>
                    <li><b>Technical:</b> {', '.join(strengths.get('technical', []) or ['None'])}</li>
                    <li><b>Projects:</b> {', '.join(strengths.get('projects', []) or ['None'])}</li>
                    <li><b>Education:</b> {', '.join(strengths.get('education', []) or ['None'])}</li>
                    <li><b>Soft Skills:</b> {', '.join(strengths.get('soft_skills', []) or ['None'])}</li>
                    </ul>

                    <h3>⚠️ <b>Weaknesses</b></h3>
                    <ul>
                    <li><b>Technical Gaps:</b> {', '.join(weaknesses.get('technical_gaps', []) or ['None'])}</li>
                    <li><b>Project/Experience Gaps:</b> {', '.join(weaknesses.get('project_or_experience_gaps', []) or ['None'])}</li>
                    <li><b>Missing Context:</b> {', '.join(weaknesses.get('missing_context', []) or ['None'])}</li>
                    </ul>

                    <h3>🛠 <b>Suggestions to improve</b></h3>
                    <ul>
                    {''.join(f'<li>{s}</li>' for s in suggestions)}
                    </ul>
                    """

                    st.markdown(f"""
                    <div class="chat-row ai">
                    <img class="avatar" src="https://img.icons8.com/ios-filled/50/1a237e/robot-2.png" alt="Tool"/>
                    <div class="chat-bubble bubble-ai">
                        <span class="sender-label">📊 Profile Analysis</span>
                        {formatted_html}
                    </div>
                    </div>
                    """, unsafe_allow_html=True)



                # --- Job fit format ---
                elif "match_score" in parsed:
                    percent = parsed["match_score"]
                    suggestions = parsed.get("suggestions", [])
                    missing = parsed.get("missing_skills", [])
                    target_role = parsed.get('target_role', 'unspecified')
                    state["target_role"]=target_role
                    suggestions_html = "<br>".join(f"• {s}" for s in suggestions)
                    missing_html = "<br>".join(f"• {s}" for s in missing)

                    st.markdown(f"""
                        <div class="chat-row ai">
                            <img class="avatar" src="https://img.icons8.com/ios-filled/50/1a237e/robot-2.png" alt="Tool"/>
                            <div class="chat-bubble bubble-ai">
                                <span class="sender-label">📊 Job Fit</span>
                                <b>🎯 Target Role:</b> {target_role}<br>
                                <div style="
                                    width: 120px; height: 120px; border-radius: 50%;
                                    background: conic-gradient(#25D366 {percent * 3.6}deg, #e0e0e0 0deg);
                                    display: flex; align-items: center; justify-content: center;
                                    font-size: 1.8rem; color: #333; margin: 10px auto;">
                                    {percent}%
                                </div>
                                <b>Missing Skills:</b><br>{missing_html}<br><br>
                                <b>Suggestions:</b><br>{suggestions_html}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

                # --- Section text format ---
                elif "result" in parsed:
                    text = parsed["result"]
                    st.markdown(f"""
                        <div class="chat-row ai">
                            <img class="avatar" src="https://img.icons8.com/ios-filled/50/1a237e/robot-2.png" alt="Tool"/>
                            <div class="chat-bubble bubble-ai">
                                <span class="sender-label">📄 Section Content</span>
                                {text}
                            </div>
                        </div>
                    """, unsafe_allow_html=True)

        else:
            st.markdown(
                f"""
                <div class="chat-row">
                    <div class="chat-bubble bubble-unknown">
                        <span class="sender-label">⚠️ Unknown</span>
                        {getattr(msg, 'content', str(msg))}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.markdown('<div style="clear:both"></div>', unsafe_allow_html=True)

st.markdown("---")

user_input = st.chat_input(
    placeholder="Ask about your LinkedIn profile, e.g., 'Analyze my profile, how do I fit for AI role, how is my about section?'" 
)

if user_input and user_input.strip():
    state.setdefault("messages", []).append(HumanMessage(content=user_input.strip()))
    validate_state(state)
    thread_id = st.session_state.get("thread_id")
    config = {"configurable": {"thread_id": thread_id}}
    with st.spinner("Processing your request..."):
        st.session_state.state = app_graph.invoke(state, config)
    st.rerun()
