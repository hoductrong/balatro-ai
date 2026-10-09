# Balatro LLM Local — Bionic / LM Studio

Mở **Balatro Local.app** trong thư mục này. UI: http://127.0.0.1:8876.

1. Chọn model đã cài → **Nạp model**. Chỉ quản lý instance `balatro-llm`, không tải model.
2. **Benchmark 5 tình huống** chạy bộ probe gốc. Đây là kiểm tra lựa chọn chiến thuật, không phải tỷ lệ thắng.
3. Chọn deck/stake/seed/số ván → **Chạy bot** bắt đầu ván mới (kết thúc ván bot dang dở nếu có). Mặc định Red/White; seed trống là ngẫu nhiên. Muốn giữ ván đang chơi, dùng **Khôi phục ván**.
4. **Tạm dừng/Tiếp tục/Dừng** chặn hành động ở ranh giới an toàn. Call đang chờ có thể mất tối đa 60 giây để kết thúc; kết quả đến sau pause bị bỏ.
5. **Khôi phục ván** dùng trajectory và trạng thái game hiện tại. Sau server restart, nạp lại model trước. Nếu game đã thoát, không tự phục hồi autosave.

Engine, luật, prompt chiến thuật, probe và kiểm tra hợp lệ lấy từ Attol8. Giữ mặc định CLI gốc: 60 giây/call, 450 calls, 750 actions, 7200 giây/ván, tối đa 6 lần thử model cho một quyết định. Không fallback. Pause tạm ngưng ngân sách thời gian.

Backend local gọi một request mỗi lần (không hedging hai request tranh tài nguyên cùng GPU); retry vẫn do runner gốc quyết định. Context 16384, parallel 1, trả JSON với tối đa 768 tokens, tắt thinking. Schema local dùng object hành động canonical rồi encode thành `action_json` cho validator gốc; không giới hạn AI ở shortlist. Request ID bị ràng buộc đúng giá trị. Qwen3.6 MLX dùng template completions vì engine cài trên máy không tắt thinking qua chat đúng cách. Các model khác dùng chat API. Điều này khác Astra low và cần benchmark riêng.

Bridge `macos/BalatroLLMLocal` chỉ hoạt động với `BALATRO_LLM_LOCAL=1`, profile bot 2. Bổ sung trạng thái công khai còn thiếu của BalatroBot 1.5.1; không đổi tiền, bài, số lượt hay điểm. Bật pool nội dung đã unlock như benchmark gốc. Sao lưu `settings.jkr` và profile 2 vào `runs/local/backups` trước khi launcher khởi động game. Bridge không hoạt động khi mở game bình thường hoặc dùng bot cũ.

Game chạy ARM64/Lovely, tốc độ x4, update 60 Hz/render 30 FPS, không render theo từng API call. Khi API thuộc một game mở bên ngoài fork, đóng game rồi chạy lại để dùng bridge riêng. Không chạy bot cũ và bot này đồng thời.

Kết quả và trajectory nằm trong `runs/local`. Lịch sử UI lưu tại `runs/local/ui-state.json`; đóng tab không dừng bot. Ván stopped/error không tính là thắng/thua. Muốn so model: cùng seed/deck/stake/phiên bản, ghi cả các ván lỗi và timeout.

CLI tương đương:

```sh
.venv/bin/python -m balatro_ai play --coach local --model balatro-llm --template qwen \
  --port PORT_GAME --seed D0000000 --output runs/my-run
```

Code gốc và bản sửa giữ giấy phép AGPL-3.0-or-later. Evidence công bố của upstream không phải kết quả model local.
