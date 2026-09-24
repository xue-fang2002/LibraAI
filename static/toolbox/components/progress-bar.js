/**
 * 进度条组件
 */
class ProgressBar {
  constructor(container) {
    this.container = container;
    this.render();
  }

  render() {
    this.container.innerHTML = `
      <div class="tb-progress-wrap" id="progress-wrap" style="display:none;">
        <div class="tb-progress-track">
          <div class="tb-progress-fill" id="progress-fill"></div>
        </div>
        <div class="tb-progress-text" id="progress-text">准备中...</div>
      </div>
    `;
    this.wrap = this.container.querySelector('#progress-wrap');
    this.fill = this.container.querySelector('#progress-fill');
    this.text = this.container.querySelector('#progress-text');
  }

  show() {
    this.wrap.style.display = 'block';
    this.set(0, '准备中...');
  }

  hide() {
    this.wrap.style.display = 'none';
  }

  set(percent, message) {
    this.fill.style.width = percent + '%';
    this.text.textContent = message || `${percent}%`;
  }

  simulate(duration = 2000, onComplete) {
    this.show();
    let p = 0;
    const interval = setInterval(() => {
      p += Math.random() * 15;
      if (p > 90) p = 90;
      this.set(Math.floor(p), `处理中... ${Math.floor(p)}%`);
      if (p >= 90) {
        clearInterval(interval);
        if (onComplete) onComplete();
      }
    }, duration / 10);
    this._interval = interval;
  }

  finish(message = '处理完成') {
    if (this._interval) clearInterval(this._interval);
    this.set(100, message);
    setTimeout(() => this.hide(), 1500);
  }

  error(message) {
    if (this._interval) clearInterval(this._interval);
    this.set(0, '❌ ' + message);
  }
}
