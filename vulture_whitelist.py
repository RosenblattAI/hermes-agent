# vulture whitelist — intentional "dead code" that vulture would otherwise flag
# These are dynamic dispatch targets, plugin hooks, and public API surface.
# Reference: https://github.com/jendrikseipp/vulture#whitelists

# Tool registry handlers — called dynamically via string dispatch in model_tools.py
def handle_terminal(): pass
def handle_file_read(): pass
def handle_file_write(): pass
def handle_file_search(): pass
def handle_patch(): pass
def handle_web_search(): pass
def handle_web_extract(): pass
def handle_browser_navigate(): pass
def handle_browser_click(): pass
def handle_browser_type(): pass
def handle_browser_snapshot(): pass
def handle_browser_scroll(): pass
def handle_browser_press(): pass
def handle_browser_vision(): pass
def handle_browser_back(): pass
def handle_browser_get_images(): pass
def handle_browser_console(): pass
def handle_delegate_task(): pass
def handle_execute_code(): pass
def handle_vision_analyze(): pass
def handle_send_message(): pass
def handle_text_to_speech(): pass
def handle_memory(): pass
def handle_clarify(): pass
def handle_todo(): pass
def handle_cronjob(): pass
def handle_process(): pass
def handle_read_file(): pass
def handle_write_file(): pass
def handle_search_files(): pass
def handle_skill_view(): pass
def handle_skills_list(): pass
def handle_skill_manage(): pass
def handle_session_search(): pass

# Gateway platform adapters — registered at import time, called via event dispatch
def setup_telegram(): pass
def setup_discord(): pass
def setup_slack(): pass
def setup_whatsapp(): pass
def setup_signal(): pass
def setup_matrix(): pass

# Plugin API — called by external plugins
def register_tool(): pass
def register_toolset(): pass
def on_message(): pass
def on_startup(): pass
def on_shutdown(): pass
