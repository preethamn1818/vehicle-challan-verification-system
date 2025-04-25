# Vehicle Challan Verification System

A web-based system for verifying vehicle challans with automatic number plate recognition support. This system allows users to check pending challans for vehicles either by entering the vehicle number manually or by uploading an image of the vehicle's number plate.

## Features

- Session-based challan verification
- Automatic number plate recognition from images
- CAPTCHA verification for security
- Detailed challan information display
- Support for both manual entry and image upload
- Real-time status updates and logging
- Responsive UI design

## Setup

1. Clone the repository:
```bash
git clone https://github.com/preethamn1818/vehicle-challan-verification-system.git
cd vehicle-challan-verification-system
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure the API endpoint:
- Open `index.html`
- Update the `apiBaseUrl` variable with your API endpoint

4. Run the backend server:
```bash
python app.py
```

5. Open `index.html` in a web browser to access the application.

## Usage

1. Click "Start Session" to begin a new verification session
2. Enter vehicle number manually or upload an image of the number plate
3. Solve the CAPTCHA verification
4. View the challan details and total pending fines

## Security Features

- Session-based verification
- CAPTCHA implementation
- Secure API endpoints
- Input validation and sanitization

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 