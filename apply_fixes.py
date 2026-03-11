"""
Скрипт для применения всех исправлений к файлам
"""

import re

def fix_ischilar_html():
    """Применить исправления к ischilar.html"""
    with open('ischilar.html', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. Добавить стрелку к селектору
    content = content.replace(
        """        .settings select {
            appearance: none;
            padding-right: 30px;
        }""",
        """        .settings select {
            appearance: none;
            padding-right: 30px;
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23666' d='M6 9L1 4h10z'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 8px center;
            background-size: 12px;
            cursor: pointer;
        }
        
        .settings select:hover {
            background-color: rgba(0,0,0,0.05);
        }"""
    )
    
    # 2. Улучшить поиск с скрытием пустых категорий
    old_search = """        // Search functionality
        document.getElementById('searchInput').addEventListener('input', function(e) {
            const query = e.target.value.toLowerCase();
            document.querySelectorAll('.profession-item').forEach(item => {
                const name = item.querySelector('.profession-name').textContent.toLowerCase();
                const skills = item.querySelector('.profession-skills')?.textContent.toLowerCase() || '';
                if (name.includes(query) || skills.includes(query)) {
                    item.style.display = 'flex';
                } else {
                    item.style.display = 'none';
                }
            });
        });"""
    
    new_search = """        // Search functionality - улучшенный поиск с скрытием пустых категорий
        document.getElementById('searchInput').addEventListener('input', function(e) {
            const query = e.target.value.toLowerCase().trim();
            
            // Если поиск пустой, показываем всё
            if (!query) {
                document.querySelectorAll('.profession-item').forEach(item => item.style.display = 'flex');
                document.querySelectorAll('.category-card').forEach(card => card.style.display = 'block');
                return;
            }
            
            // Фильтруем профессии
            document.querySelectorAll('.profession-item').forEach(item => {
                const name = item.querySelector('.profession-name').textContent.toLowerCase();
                const skills = item.querySelector('.profession-skills')?.textContent.toLowerCase() || '';
                if (name.includes(query) || skills.includes(query)) {
                    item.style.display = 'flex';
                } else {
                    item.style.display = 'none';
                }
            });
            
            // Скрываем категории без видимых профессий
            document.querySelectorAll('.category-card').forEach(card => {
                const visibleProfessions = Array.from(card.querySelectorAll('.profession-item'))
                    .filter(item => item.style.display !== 'none');
                card.style.display = visibleProfessions.length > 0 ? 'block' : 'none';
            });
        });"""
    
    content = content.replace(old_search, new_search)
    
    # 3. Улучшить теги поиска
    old_tags = """        // Search tags functionality
        document.querySelectorAll('.search-tag').forEach(tag => {
            tag.addEventListener('click', function() {
                const searchTerm = this.getAttribute('data-search');
                document.getElementById('searchInput').value = searchTerm;
                document.querySelectorAll('.profession-item').forEach(item => {
                    const name = item.querySelector('.profession-name').textContent.toLowerCase();
                    if (name.includes(searchTerm.toLowerCase())) {
                        item.style.display = 'flex';
                    } else {
                        item.style.display = 'none';
                    }   
                });
            });
        });"""
    
    new_tags = """        // Search tags functionality
        document.querySelectorAll('.search-tag').forEach(tag => {
            tag.addEventListener('click', function() {
                const searchTerm = this.getAttribute('data-search');
                document.getElementById('searchInput').value = searchTerm;
                
                // Фильтруем профессии
                document.querySelectorAll('.profession-item').forEach(item => {
                    const name = item.querySelector('.profession-name').textContent.toLowerCase();
                    const skills = item.querySelector('.profession-skills')?.textContent.toLowerCase() || '';
                    if (name.includes(searchTerm.toLowerCase()) || skills.includes(searchTerm.toLowerCase())) {
                        item.style.display = 'flex';
                    } else {
                        item.style.display = 'none';
                    }   
                });
                
                // Скрываем пустые категории
                document.querySelectorAll('.category-card').forEach(card => {
                    const visibleProfessions = Array.from(card.querySelectorAll('.profession-item'))
                        .filter(item => item.style.display !== 'none');
                    card.style.display = visibleProfessions.length > 0 ? 'block' : 'none';
                });
            });
        });"""
    
    content = content.replace(old_tags, new_tags)
    
    with open('ischilar.html', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print('✅ ischilar.html исправлен')

if __name__ == '__main__':
    try:
        fix_ischilar_html()
        print('\n🎉 Все исправления применены!')
    except Exception as e:
        print(f'❌ Ошибка: {e}')
