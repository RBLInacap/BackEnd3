window.chileanMobileDigits = function (value) {
  let digits = String(value || '').replace(/\D/g, '');

  if (digits.startsWith('569') && digits.length === 11) {
    digits = digits.slice(3);
  } else if (digits.startsWith('56') && digits.length === 10) {
    digits = digits.slice(2);
    if (digits.startsWith('9')) digits = digits.slice(1);
  } else if (digits.startsWith('9') && digits.length === 9) {
    digits = digits.slice(1);
  }

  return digits.slice(0, 8);
};

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-digits-only]').forEach(input => {
    const keepDigitsOnly = () => {
      input.value = input.value.replace(/\D/g, '');
    };
    keepDigitsOnly();
    input.addEventListener('input', keepDigitsOnly);
  });

  document.querySelectorAll('[data-chilean-mobile]').forEach(input => {
    input.value = window.chileanMobileDigits(input.value);
    input.addEventListener('input', () => {
      input.value = window.chileanMobileDigits(input.value);
    });
  });
});
