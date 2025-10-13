/* Dodanie skryptu dla przełącznika */
document.addEventListener('DOMContentLoaded', () => {
    const toggle = document.getElementById('theme-toggle');
    const body = document.body;

    // Sprawdzenie preferencji użytkownika lub lokalnego storage
    const currentTheme = localStorage.getItem('theme') || 'light';
    if (currentTheme === 'dark') {
        body.classList.add('dark-mode');
        toggle.textContent = '☀️'; // Słońce dla Dark Mode
    } else {
        toggle.textContent = '🌙'; // Księżyc dla Light Mode
    }

    toggle.addEventListener('click', () => {
        body.classList.toggle('dark-mode');
        const theme = body.classList.contains('dark-mode') ? 'dark' : 'light';
        localStorage.setItem('theme', theme);
        toggle.textContent = theme === 'dark' ? '☀️' : '🌙';
    });
});