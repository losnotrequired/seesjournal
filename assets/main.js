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
})();
