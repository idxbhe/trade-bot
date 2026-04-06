Dokumen ini berisi logika algoritma, parameter kuantitatif, dan arsitektur manajemen risiko untuk pengembangan bot trading otomatis di bursa Binance, Bybit, dan KuCoin. Fokus utama: Akumulasi profit kecil secara konsisten dengan risiko minimal.

## 1. Filter Pemilihan Aset (Market Selection)
Bot wajib melakukan screening aset secara real-time berdasarkan data buku pesanan dan volume:
- **Likuiditas:** Volume perdagangan 24 jam minimal > $10.000.000 untuk memastikan eksekusi lancar.
- **Efisiensi Spread:** Spread bid-ask harus < 0,15%. Jika spread melebar, bot dilarang masuk posisi.
- **Kedalaman Pasar (Depth):** Minimal tersedia likuiditas senilai $100.000 pada rentang ±2% dari harga pasar saat ini.
- **Aset Prioritas:** Fokus pada pasangan dengan likuiditas tinggi seperti BTC/USDT, ETH/USDT, SOL/USDT, dan XRP/USDT.

## 2. Arsitektur Strategi Trading

### A. Strategi Neutral Grid (Kondisi Sideways)
Digunakan saat pasar tidak memiliki tren kuat (ADX < 20).
- **Logika:** Memasang jaring pesanan beli dan jual pada interval persentase tetap (Geometris) dalam rentang harga tertentu.
- **Formulasi Jarak:** $P_i = L \times (1 + r)^i$, di mana $L$ adalah batas harga bawah dan $r$ adalah rasio persentase antar level grid.
- **Target Profit:** 0,5% - 1,0% per siklus grid setelah dikurangi biaya transaksi.
- **Automasi:** Gunakan pesanan OCO (One-Cancels-the-Other) untuk setiap grid level guna memastikan perlindungan instan.

### B. Strategi Mean Reversion (Kondisi Volatil - 15m)
Digunakan untuk mengeksploitasi overreaction pasar pada timeframe 15 menit.
- **Konfigurasi Indikator:**
  - Bollinger Bands: Periode 20, Standar Deviasi 2.
  - RSI (Relative Strength Index): Periode 14 dengan level 70 (Overbought) dan 30 (Oversold).
- **Logika Entri (Buy):** Harga menyentuh atau menembus Lower Band dan RSI < 30.
- **Logika Keluar (Exit):** Harga kembali ke Middle Band (SMA 20) atau menggunakan Trailing Take Profit sebesar 1,5%.

## 3. Protokol Manajemen Risiko (Mandatori)
Setiap eksekusi order harus melewati filter risiko otomatis sebelum dikirim ke API bursa:
- **Risk per Trade:** Maksimal 1% - 2% dari total ekuitas akun dipertaruhkan per posisi.
- **Position Sizing:** 
  $Ukuran Posisi = \frac{Saldo Akun \times Persentase Risiko}{Harga Entri - Harga Stop Loss}$.
- **Stop Loss Dinamis:** Menggunakan Average True Range (ATR) dengan multiplier 1,5x atau 2x untuk menyesuaikan dengan volatilitas pasar.
- **Circuit Breaker:** Jika saldo akun turun 5% dalam 24 jam, bot harus menghentikan seluruh aktivitas trading dan membatalkan semua order aktif.

## 4. Logika Eksekusi API & Optimalisasi Biaya
- **Maker-Only Execution:** Gunakan instruksi 'Post-Only' pada semua limit order untuk memastikan bot mendapatkan tarif biaya 'Maker' (lebih murah) dan menghindari slippage.
- **Biaya Transaksi:** Aktifkan fitur pembayaran biaya menggunakan token bursa (BNB/KCS) untuk mendapatkan diskon tambahan hingga 25%.
- **Rate Limiting:** Sesuaikan frekuensi permintaan API dengan limit bursa (Binance: 1.200 req/menit; Bybit: hingga 400 req/detik pada akun tertentu) guna menghindari IP suspension.

## 5. Validasi Sistem
Bot dilarang berjalan di Live Market tanpa melewati:
1. **Backtesting:** Uji strategi pada data historis minimal 1 tahun dengan simulasi slippage 0,1%.
2. **Forward Testing (Paper Trading):** Uji bot di lingkungan real-time menggunakan saldo virtual selama minimal 2 minggu untuk memantau latensi eksekusi.

