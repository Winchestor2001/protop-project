"""
Скрипт для обновления ссылок в kliyent.html и ischilar.html
чтобы они правильно вели на specialists.html
"""

import re

def update_kliyent_html():
    """Обновить ссылки в kliyent.html для клиентов"""
    with open('kliyent.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Заменяем все ссылки specialists.html на specialists.html?profession=...&mode=client
    content = re.sub(
        r'href="specialists\.html\?profession=([^"]+)"',
        r'href="specialists.html?profession=\1&mode=client"',
        content
    )
    
    with open('kliyent.html', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print('✅ kliyent.html обновлен')

def update_ischilar_html():
    """Обновить кнопки в ischilar.html для работников"""
    with open('ischilar.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Находим все кнопки с классом select-profession-btn и data-profession атрибутом
    # и заменяем их на ссылки
    pattern = r'<button class="select-profession-btn" data-profession="([^"]+)" data-lang-key="select-btn">Tanlash</button>'
    replacement = r'<a href="specialists.html?profession=\1&mode=worker" class="select-profession-btn" data-profession="\1" data-lang-key="select-btn" style="text-decoration: none;">Tanlash</a>'
    
    content = re.sub(pattern, replacement, content)
    
    with open('ischilar.html', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print('✅ ischilar.html обновлен')

if __name__ == '__main__':
    try:
        update_kliyent_html()
        update_ischilar_html()
        print('\n🎉 Все файлы успешно обновлены!')
    except Exception as e:
        print(f'❌ Ошибка: {e}')
