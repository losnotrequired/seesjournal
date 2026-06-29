(function(){
  var menu = document.getElementById('menu');
  var hamb = document.getElementById('hamb');
  var close = document.getElementById('close');
  if (hamb && menu) hamb.addEventListener('click', function(){ menu.classList.add('open'); });
  if (close && menu) close.addEventListener('click', function(){ menu.classList.remove('open'); });
  Array.prototype.forEach.call(document.querySelectorAll('[data-menu]'), function(a){
    a.addEventListener('click', function(){ if (menu) menu.classList.remove('open'); });
  });

  var totop = document.getElementById('totop');
  if (totop){
    window.addEventListener('scroll', function(){
      if (window.scrollY > 600) totop.classList.add('show'); else totop.classList.remove('show');
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-share]'), function(a){
    a.addEventListener('click', function(e){
      e.preventDefault();
      if (navigator.share){ navigator.share({ title: document.title, url: location.href }).catch(function(){}); }
      else if (navigator.clipboard){ navigator.clipboard.writeText(location.href); a.textContent = '\u2713'; }
    });
  });

  // Placeholder guard: some venue pages serve a tiny lazy-load/spacer image that "loads"
  // successfully, so the card's inline onerror never fires and faint overlay text is left
  // sitting on a blank panel. Treat a loaded-but-tiny card photo like a failed image: drop
  // has-photo, turn the panel blue, and remove the img so the text becomes readable.
  function flagPlaceholder(img){
    var panel = img.closest('.card__panel');
    if (!panel) return;
    panel.classList.remove('has-photo');
    panel.classList.add('is-blue');
    img.remove();
  }
  function checkPhoto(img){
    if (img.naturalWidth > 0 && img.naturalWidth < 64) flagPlaceholder(img);
  }
  Array.prototype.forEach.call(document.querySelectorAll('.card__photo'), function(img){
    if (img.complete) checkPhoto(img);
    else img.addEventListener('load', function(){ checkPhoto(img); });
  });
})();
