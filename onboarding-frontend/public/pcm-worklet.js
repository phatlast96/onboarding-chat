class PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.carry = new Float32Array(0);
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    const ratio = sampleRate / 24000;
    const merged = new Float32Array(this.carry.length + channel.length);
    merged.set(this.carry);
    merged.set(channel, this.carry.length);

    const count = Math.floor(merged.length / ratio);
    const pcm = new Int16Array(count);
    for (let i = 0; i < count; i++) {
      const sample = Math.max(-1, Math.min(1, merged[Math.floor(i * ratio)]));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }

    const used = Math.floor(count * ratio);
    this.carry = merged.slice(used);
    if (pcm.length) this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}

registerProcessor("pcm-processor", PcmProcessor);
