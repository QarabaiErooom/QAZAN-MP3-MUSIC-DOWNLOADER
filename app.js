(() => {
  'use strict';

  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const nav = document.querySelector('.nav');
  const navToggle = document.getElementById('nav-toggle');
  const navLinks = document.getElementById('nav-links');

  /* Mobile navigation */
  const closeMenu = () => {
    if (!navLinks || !navToggle) return;
    navLinks.classList.remove('open');
    navToggle.setAttribute('aria-expanded', 'false');
  };

  if (navToggle && navLinks) {
    navToggle.addEventListener('click', () => {
      const open = navLinks.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', String(open));
    });

    navLinks.addEventListener('click', (event) => {
      if (event.target.closest('a')) closeMenu();
    });

    document.addEventListener('click', (event) => {
      if (nav && !nav.contains(event.target)) closeMenu();
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeMenu();
    });

    window.addEventListener('resize', () => {
      if (window.innerWidth > 900) closeMenu();
    });
  }

  /* Smooth anchor scrolling */
  document.querySelectorAll('a[href^="#"]').forEach((link) => {
    link.addEventListener('click', (event) => {
      const hash = link.getAttribute('href');
      if (!hash || hash === '#') return;
      const target = document.querySelector(hash);
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: prefersReduced ? 'auto' : 'smooth', block: 'start' });
      if (history.pushState) history.pushState(null, '', hash);
      else window.location.hash = hash;
    });
  });

  /* Copy-to-clipboard for code blocks */
  const copyText = async (text) => {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(area);
    }
  };

  document.querySelectorAll('.copy-btn').forEach((button) => {
    button.addEventListener('click', async () => {
      const block = button.closest('.code-block');
      const code = block ? block.querySelector('code') : null;
      if (!code) return;
      const original = button.textContent;
      try {
        await copyText(code.innerText);
        button.textContent = 'Copied!';
        button.classList.add('copied');
      } catch (error) {
        button.textContent = 'Failed';
      }
      window.setTimeout(() => {
        button.textContent = original;
        button.classList.remove('copied');
      }, 1500);
    });
  });

  /* Scroll reveal */
  const revealEls = Array.from(document.querySelectorAll('.reveal'));
  if (prefersReduced || !('IntersectionObserver' in window)) {
    revealEls.forEach((element) => element.classList.add('visible'));
  } else {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, { rootMargin: '0px 0px -40px 0px', threshold: 0.08 });
    revealEls.forEach((element) => observer.observe(element));
  }

  /* Back-to-top button */
  const backToTop = document.getElementById('back-to-top');
  if (backToTop) {
    const onScroll = () => {
      backToTop.classList.toggle('show', window.scrollY > 300);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    backToTop.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: prefersReduced ? 'auto' : 'smooth' });
    });
  }

  /* Active navigation link on scroll */
  const navAnchors = Array.from(document.querySelectorAll('.nav-links a[href^="#"]'));
  const sections = navAnchors
    .map((link) => {
      const id = link.getAttribute('href').slice(1);
      const element = document.getElementById(id);
      return element ? { id, element, link } : null;
    })
    .filter(Boolean);

  const setActive = () => {
    if (!sections.length) return;
    const position = window.scrollY + 140;
    let current = null;
    sections.forEach((section) => {
      const top = section.element.getBoundingClientRect().top + window.scrollY;
      if (top <= position) current = section;
    });
    navAnchors.forEach((link) => link.classList.remove('active'));
    if (current) current.link.classList.add('active');
  };

  window.addEventListener('scroll', setActive, { passive: true });
  window.addEventListener('resize', setActive);
  setActive();
})();