import os
import pandas as pd
from openpyxl import Workbook
import csv
from flask import Flask, render_template, request, jsonify, flash, redirect

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Needed for flash messages

# Initially set default folder paths
input_folder = 'INPUT'
output_folder = 'SMOOTH'
month_values = []

def process_file(file_path, output_folder):
    file_name = os.path.basename(file_path)
    if file_name.endswith('.xls') or file_name.endswith('.xlsx'):
        output_file_path = os.path.join(output_folder, os.path.splitext(file_name)[0] + '.xlsx')
        try:
            if file_name.endswith('.xls'):
                with open(file_path, 'r', encoding='utf-8') as file:
                    csv_reader = csv.reader(file, delimiter='\t')
                    data = [row for row in csv_reader]
                month_values.append(data[1][1])
                data = data[6:]
                wb = Workbook()
                ws = wb.active
                for row in data:
                    ws.append(row)
                wb.save(output_file_path)
            else:
                # For .xlsx files
                data = pd.read_excel(file_path)
                data.to_excel(output_file_path, index=False)
            return True
        except Exception as e:
            print(f"Error processing {file_name}: {e}")
            return False
    else:
        print(f"File {file_name} is not an .xls or .xlsx file.")
        return False


@app.route('/')
def index():
    return render_template('index.html', month_values=month_values)

@app.route('/update_folders', methods=['POST'])
def update_folders():
    global input_folder, output_folder
    input_folder = request.json.get('input_folder', 'INPUT').strip()
    output_folder = request.json.get('output_folder', 'SMOOTH').strip()
    
    input_folder = input_folder.strip('"').strip("'")
    output_folder = output_folder.strip('"').strip("'")
    
    if not os.path.exists(input_folder):
        os.makedirs(input_folder)
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    
    return jsonify({'message': 'Folders updated successfully'})

@app.route('/process_tab', methods=['POST'])
def process_tab_route():
    try:
        success_count = 0
        fail_count = 0
        for file_name in os.listdir(input_folder):
            file_path = os.path.join(input_folder, file_name)
            if process_file(file_path, output_folder):
                success_count += 1
            else:
                fail_count += 1
        return jsonify({'message': f'Tab processed successfully: {success_count} files succeeded, {fail_count} files failed'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/upload', methods=['POST'])
def upload():
    if 'files' not in request.files:
        flash('No file part')
        return redirect(request.url)
    files = request.files.getlist('files')
    for file in files:
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        if file and (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            file_path = os.path.join(input_folder, file.filename)
            print(f"Saving file {file.filename} to {file_path}...")
            file.save(file_path)
            if process_file(file_path, output_folder):
                flash(f'File {file.filename} successfully uploaded and processed')
            else:
                flash(f'File {file.filename} upload failed or could not be processed')
        else:
            flash(f'Invalid file format for {file.filename}. Only .xls and .xlsx are allowed.')
    return redirect('/')



@app.route('/clear_uploads', methods=['POST'])
def clear_uploads():
    try:
        # Clear the input folder
        for file_name in os.listdir(input_folder):
            file_path = os.path.join(input_folder, file_name)
            os.remove(file_path)
        
        # Clear the smooth folder
        for file_name in os.listdir(output_folder):
            file_path = os.path.join(output_folder, file_name)
            os.remove(file_path)
        
        month_values.clear()
        return jsonify({'message': 'Uploads cleared successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/process', methods=['POST'])
def process():
    try:
        dfs = []
        for filename in os.listdir(output_folder):
            if filename.endswith(".xlsx"):
                df = pd.read_excel(os.path.join(output_folder, filename))
                dfs.append(df)
        if not dfs:
            return jsonify({'error': 'No files to process'}), 400

        combined_df = pd.concat(dfs, ignore_index=True)
        df = combined_df
        df['Lender Received Date'] = pd.to_datetime(df['Lender Received Date'])
        df['Lender Filled Date'] = pd.to_datetime(df['Lender Filled Date'])
        df['Borrower Filled Date'] = pd.to_datetime(df['Borrower Filled Date'])
        df['DaystoRes'] = (df['Lender Filled Date'] - df['Lender Received Date']).dt.days
        df['DaystoReceive'] = (df['Borrower Filled Date'] - df['Lender Received Date']).dt.days
        df['Clean Charge'] = df['Lending Charges'].str.extract('(\d+\.\d+)')
        df['Clean Charge'] = df['Clean Charge'].astype(float)

        min_data = float(request.json.get('min_data', 0.1))
        max_price = float(request.json.get('max_price', 0.1))
        turnaround_time = float(request.json.get('turnaround_time', 0.1))
        toggle_var = request.json.get('toggle_var', 'Article')

        min_data = max(min_data, 0.1)
        max_price = max(max_price, 0.1)
        turnaround_time = max(turnaround_time, 0.1)
        
        if toggle_var == 'Article':
            base = df[df['Photocopy Flag'] == 1]
            time_field = 'DaystoRes'
        elif toggle_var == 'Loan':
            base = df[df['Photocopy Flag'] == 0]
            time_field = 'DaystoReceive'
        else:
            return jsonify({'error': 'Invalid type selected'}), 400

        symbolgroup = base.groupby(['Lender Symbol'])
        symbolsclean = symbolgroup.filter(lambda x: len(x) >= min_data)
        symbolgroup = symbolsclean.groupby(['Lender Symbol'])
        meaned = symbolgroup.mean(numeric_only=True)

        fastest_symbols = meaned.sort_values(by=time_field, ascending=True)
        chopchop = fastest_symbols[fastest_symbols[time_field] < turnaround_time]
        fastandcheap = chopchop[chopchop['Clean Charge'] < max_price]

        result = {
            'symbols': fastandcheap.index.tolist(),
            'detailed': [
                {
                    'symbol': symbol,
                    'transactions': len(symbolgroup.get_group(symbol)),
                    'price': f"{fastandcheap.loc[symbol, 'Clean Charge']:.2f}",
                    'turnaround_time': f"{fastandcheap.loc[symbol, time_field]:.1f}"
                }
                for symbol in fastandcheap.index
            ]
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
