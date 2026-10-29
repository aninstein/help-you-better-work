/* Continuous capture, anti-alias resampling and sample-clocked protocol 3. */
class DuetCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.protocol = options?.processorOptions?.protocol || 2;
    this.samples = [];
    this.sequence = 0;
    this.sampleStart = 0;
    this.inputClock = 0;
    this.nextOutput = 0;
    this.ratio = sampleRate / 16000;
    this.history = new Float32Array(64);
    this.phases = [];
    // Windowed-sinc fractional delay low pass; native 16k bypasses resampling.
    const cutoff = .46 * Math.min(1, 16000 / sampleRate);
    for (let phase = 0; phase < 128; phase++) {
      const weights = new Float32Array(63);
      let sum = 0;
      for (let k = 0; k < 63; k++) {
        const x = k - 31 - phase / 128;
        const sinc = Math.abs(x) < 1e-8 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * x) / (Math.PI * x);
        weights[k] = sinc * (.42 - .5 * Math.cos(2 * Math.PI * k / 62) + .08 * Math.cos(4 * Math.PI * k / 62));
        sum += weights[k];
      }
      for (let k = 0; k < 63; k++) weights[k] /= sum;
      this.phases.push(weights);
    }
  }
  emit(value) {
    const sample = Math.max(-1, Math.min(1, value));
    this.samples.push(sample < 0 ? sample * 32768 : sample * 32767);
    if (this.samples.length !== 512) return;
    const header = this.protocol === 3 ? 20 : 0;
    const packet = new ArrayBuffer(1024 + header);
    const pcm = new Int16Array(packet, header);
    pcm.set(this.samples);
    if (header) {
      const view = new DataView(packet);
      view.setUint32(0, 0x33505644, true); // DVP3
      view.setUint32(4, this.sequence, true);
      view.setBigUint64(8, BigInt(this.sampleStart), true);
      view.setUint16(16, 512, true);
    }
    let energy = 0;
    for (const value of pcm) energy += (value / 32768) ** 2;
    // Clocks advance even if the main thread drops this frame under backpressure.
    this.sequence = (this.sequence + 1) >>> 0;
    this.sampleStart += 512;
    this.port.postMessage({ pcm: packet, level: Math.sqrt(energy / 512) }, [packet]);
    this.samples.length = 0;
  }
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const value of input) {
      if (sampleRate === 16000) {
        this.emit(value);
        continue;
      }
      const index = this.inputClock++;
      this.history[index % 64] = value;
      while (this.nextOutput <= index) {
        const phase = Math.min(127, Math.round((index - this.nextOutput) * 128));
        const weights = this.phases[phase];
        let filtered = 0;
        for (let k = 0; k < 63; k++) filtered += weights[k] * this.history[(index - k + 128) % 64];
        this.emit(filtered);
        this.nextOutput += this.ratio;
      }
    }
    return true;
  }
}
registerProcessor("duet-capture", DuetCapture);
