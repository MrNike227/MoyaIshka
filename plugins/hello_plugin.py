# Пример простого плагина. При включении Python-плагинов команда /hello ответит приветствием.
PLUGIN_NAME = "hello_plugin"
PLUGIN_DESCRIPTION = "Демонстрационный плагин-приветствие"


def handle_command(text, api):
    if text.strip().lower() == "/hello":
        return "Привет! Это пример собственного плагина для Моей ИИшки."
    return None
