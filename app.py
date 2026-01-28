from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
import requests
from requests.exceptions import RequestException, Timeout
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
import logging
import secrets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
CORS(app)


class QuestionExtractor:
    """Enhanced question extraction with better parsing logic"""
    
    def __init__(self):
        self.question_types = {
            'mcq': 'Multiple Choice',
            'tf': 'True or False',
            'cloze': 'Fill in the Blank (Cloze)',
            'matching': 'Matching Type',
            'checkbox': 'Multiple Select',
            'text': 'Short Answer',
            'essay': 'Essay',
            'identification': 'Identification'
        }
    
    def extract_questions(self, html: str, show_output: bool = True) -> List[Dict]:
        """Extract questions from HTML with improved parsing."""
        if show_output:
            logger.info("Extracting questions from HTML...")
        
        soup = BeautifulSoup(html, 'html.parser')
        questions = []
        
        question_divs = soup.find_all('div', class_='que')
        
        if not question_divs:
            logger.warning("No question containers found with class 'que'")
            return []
        
        for idx, q_div in enumerate(question_divs, 1):
            try:
                question_data = self._parse_question(soup, q_div, idx)
                if question_data and question_data.get('question_text'):
                    questions.append(question_data)
            except Exception as e:
                logger.error(f"Error parsing question {idx}: {str(e)}")
                continue
        
        if show_output:
            logger.info(f"Successfully extracted {len(questions)} questions")
        
        return questions
    
    def _parse_question(self, soup: BeautifulSoup, q_div, number: int) -> Dict:
        """Parse a single question div into structured data"""
        question_data = {
            'number': number,
            'type': 'Unknown',
            'question_text': '',
            'choices': [],
            'blanks': [],
            'metadata': {}
        }
        
        question_data['question_text'] = self._extract_question_text(q_div)
        answer_div = self._find_answer_container(q_div)
        
        if not answer_div:
            logger.warning(f"Question {number}: No answer container found")
            return question_data
        
        self._detect_and_extract(soup, answer_div, question_data)
        question_data['choices'] = self._clean_choices(question_data['choices'])
        
        return question_data
    
    def _extract_question_text(self, q_div) -> str:
        """Extract and clean question text"""
        qtext_div = q_div.find('div', class_='qtext')
        
        if not qtext_div:
            return ""
        
        for element in qtext_div(['script', 'style', 'noscript', 'iframe']):
            element.decompose()
        
        text = qtext_div.get_text(separator=' ', strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        
        text = re.sub(r'Question\s+\d+\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Not yet answered.*?$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Marked? out of.*?$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Flag question.*?$', '', text, flags=re.IGNORECASE)
        
        instruction_match = re.search(r'Instructions?:\s*(.+?)(?=\n|$)', text, re.IGNORECASE)
        if instruction_match:
            text = re.sub(r'Instructions?:.*?(?=\n|$)', '', text, flags=re.IGNORECASE)
            text = text.strip()
        
        return text.strip()
    
    def _find_answer_container(self, q_div):
        """Find the container with answer options"""
        for class_name in ['answer', 'formulation', 'ablock']:
            container = q_div.find('div', class_=class_name)
            if container:
                return container
        return q_div
    
    def _detect_and_extract(self, soup: BeautifulSoup, answer_div, question_data: Dict):
        """Detect question type and extract choices"""
        
        radios = answer_div.find_all('input', {'type': 'radio'})
        if radios:
            self._extract_radio_choices(soup, radios, question_data)
            return
        
        selects = answer_div.find_all('select')
        if selects:
            self._extract_select_choices(selects, answer_div, question_data)
            return
        
        checkboxes = answer_div.find_all('input', {'type': 'checkbox'})
        if checkboxes:
            self._extract_checkbox_choices(soup, checkboxes, question_data)
            return
        
        text_inputs = answer_div.find_all('input', {'type': 'text'})
        if text_inputs:
            self._classify_text_input(question_data)
            return
        
        textareas = answer_div.find_all('textarea')
        if textareas:
            question_data['type'] = self.question_types['essay']
            return
        
        logger.warning(f"Question {question_data['number']}: Could not detect question type")
    
    def _extract_radio_choices(self, soup: BeautifulSoup, radios, question_data: Dict):
        """Extract choices from radio buttons with improved label finding"""
        choices = []
        seen_texts = set()
        
        for radio in radios:
            label_text = self._find_label_text(soup, radio)
            
            if label_text and label_text not in seen_texts:
                seen_texts.add(label_text)
                choices.append(label_text)
        
        question_data['choices'] = choices
        
        if len(choices) == 2:
            choice_texts_lower = [c.lower() for c in choices]
            if ('true' in choice_texts_lower and 'false' in choice_texts_lower) or \
               ('yes' in choice_texts_lower and 'no' in choice_texts_lower) or \
               ('correct' in choice_texts_lower and 'incorrect' in choice_texts_lower):
                question_data['type'] = self.question_types['tf']
                return
        
        question_data['type'] = self.question_types['mcq']
    
    def _find_label_text(self, soup: BeautifulSoup, input_element) -> Optional[str]:
        """Find label text for an input element using multiple strategies."""
        label_text = None
        input_id = input_element.get('id')
        
        if input_id:
            label = soup.find('label', {'for': input_id})
            if label:
                label_text = label.get_text(strip=True)
        
        if not label_text:
            parent_label = input_element.find_parent('label')
            if parent_label:
                label_copy = parent_label.__copy__()
                for inp in label_copy.find_all('input'):
                    inp.decompose()
                label_text = label_copy.get_text(strip=True)
        
        if not label_text:
            parent = input_element.find_parent()
            if parent:
                sibling_label = parent.find('label')
                if sibling_label:
                    label_text = sibling_label.get_text(strip=True)
        
        if not label_text:
            parent = input_element.find_parent(['div', 'span', 'td', 'li'])
            if parent:
                parent_copy = parent.__copy__()
                for inp in parent_copy.find_all('input'):
                    inp.decompose()
                label_text = parent_copy.get_text(strip=True)
        
        if not label_text:
            next_sibling = input_element.next_sibling
            if next_sibling and hasattr(next_sibling, 'strip'):
                label_text = next_sibling.strip()
        
        if label_text:
            label_text = self._clean_label_text(label_text)
        
        return label_text if label_text else None
    
    def _clean_label_text(self, text: str) -> str:
        """Clean label text by removing prefixes and artifacts"""
        if not text:
            return ""
        
        text = re.sub(r'^[a-zA-Z0-9]\.\s*', '', text)
        text = re.sub(r'^\([a-zA-Z0-9]\)\s*', '', text)
        text = re.sub(r'^[a-zA-Z0-9]\)\s*', '', text)
        text = text.strip()
        text = re.sub(r'Not yet answered', '', text, flags=re.IGNORECASE)
        text = re.sub(r'Marked out of.*', '', text, flags=re.IGNORECASE)
        
        return text.strip()
    
    def _extract_select_choices(self, selects, answer_div, question_data: Dict):
        """Extract choices from select dropdowns"""
        num_selects = len(selects)
        
        q_text_lower = question_data['question_text'].lower()
        is_matching = any(keyword in q_text_lower for keyword in [
            'match', 'matching', 'connect', 'pair', 'correspond'
        ])
        
        if num_selects == 1:
            question_data['type'] = 'Dropdown Select'
            question_data['choices'] = self._extract_options_from_select(selects[0])
            return
        
        if is_matching or num_selects <= 15:
            first_select = selects[0]
            choices = self._extract_options_from_select(first_select)
            items = self._extract_matching_items(answer_div, choices)
            
            if items and len(items) >= num_selects - 2:
                question_data['type'] = self.question_types['matching']
                question_data['choices'] = choices
                question_data['blanks'] = items
                return
        
        if num_selects > 1:
            first_options = set(self._extract_options_from_select(selects[0]))
            all_same = all(
                set(self._extract_options_from_select(s)) == first_options 
                for s in selects[1:]
            )
            
            if all_same:
                question_data['type'] = self.question_types['matching']
                question_data['choices'] = list(first_options)
                items = self._extract_matching_items(answer_div, question_data['choices'])
                question_data['blanks'] = items if items else [f"Item {i+1}" for i in range(num_selects)]
                return
        
        question_data['type'] = self.question_types['cloze']
        question_data['blanks'] = [f"Blank {i+1}" for i in range(num_selects)]
        
        all_options = set()
        for select in selects:
            options = self._extract_options_from_select(select)
            all_options.update(options)
        
        question_data['choices'] = sorted(list(all_options))
    
    def _extract_options_from_select(self, select) -> List[str]:
        """Extract clean options from a select element"""
        options = []
        seen = set()
        
        for option in select.find_all('option'):
            text = option.get_text(strip=True)
            value = option.get('value', '')
            
            if not text or not value:
                continue
            
            if text.lower() in ['choose...', 'select...', '---', 'please select']:
                continue
            
            if text not in seen:
                seen.add(text)
                options.append(text)
        
        return options
    
    def _extract_matching_items(self, answer_div, choices: List[str]) -> List[str]:
        """Extract items to be matched from the answer container"""
        items = []
        
        table = answer_div.find('table')
        if table:
            rows = table.find_all('tr')
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    term_cell = cells[0]
                    term_cell_copy = term_cell.__copy__()
                    for select in term_cell_copy.find_all('select'):
                        select.decompose()
                    
                    term = term_cell_copy.get_text(strip=True)
                    
                    if term and term not in choices and len(term) > 1:
                        term = re.sub(r'^\d+\.\s*', '', term)
                        term = term.strip()
                        if term:
                            items.append(term)
        
        if not items:
            containers = []
            for tag in ['div', 'li', 'p']:
                containers.extend(answer_div.find_all(tag, recursive=True))
            
            for container in containers:
                direct_select = container.find('select', recursive=False)
                if not direct_select:
                    for child in container.children:
                        if hasattr(child, 'find') and child.find('select'):
                            direct_select = child.find('select')
                            break
                
                if direct_select:
                    container_copy = container.__copy__()
                    
                    for select in container_copy.find_all('select'):
                        select.decompose()
                    
                    for label_elem in container_copy.find_all(['label']):
                        if 'Choose' in label_elem.get_text():
                            label_elem.decompose()
                    
                    text = container_copy.get_text(strip=True)
                    text = re.sub(r'^\d+\.\s*', '', text)
                    text = re.sub(r'^[•\-]\s*', '', text)
                    text = text.strip()
                    
                    if text and text not in choices and len(text) > 1:
                        if not text.lower().startswith('choose') and text not in items:
                            items.append(text)
        
        if not items:
            for select in answer_div.find_all('select'):
                parent = select.find_parent()
                if parent:
                    text_parts = []
                    for sibling in parent.children:
                        if sibling == select:
                            break
                        if hasattr(sibling, 'get_text'):
                            text_parts.append(sibling.get_text(strip=True))
                        elif isinstance(sibling, str):
                            text_parts.append(sibling.strip())
                    
                    text = ' '.join(text_parts).strip()
                    text = re.sub(r'^\d+\.\s*', '', text)
                    text = re.sub(r'^[•\-]\s*', '', text)
                    
                    if text and text not in choices and len(text) > 1:
                        if not text.lower().startswith('choose') and text not in items:
                            items.append(text)
        
        return items
    
    def _extract_checkbox_choices(self, soup: BeautifulSoup, checkboxes, question_data: Dict):
        """Extract choices from checkboxes"""
        choices = []
        seen_texts = set()
        
        if 'blank' in question_data['question_text'].lower():
            unique_names = set([cb.get('name') for cb in checkboxes if cb.get('name')])
            question_data['blanks'] = [f"Blank {i+1}" for i in range(len(unique_names))]
            question_data['type'] = 'Fill in the Blank (Checkbox)'
        else:
            question_data['type'] = self.question_types['checkbox']
        
        for checkbox in checkboxes:
            label_text = self._find_label_text(soup, checkbox)
            
            if label_text and label_text not in seen_texts:
                if label_text.lower() not in ['flag question', 'select all']:
                    seen_texts.add(label_text)
                    choices.append(label_text)
        
        question_data['choices'] = choices
    
    def _classify_text_input(self, question_data: Dict):
        """Classify text input questions based on question text"""
        q_text_lower = question_data['question_text'].lower()
        
        identification_keywords = [
            'identify', 'who is', 'what is', 'name the', 'who are', 'what are',
            'give the name', 'state the name', 'mention the name'
        ]
        
        if any(keyword in q_text_lower for keyword in identification_keywords):
            question_data['type'] = self.question_types['identification']
        else:
            question_data['type'] = self.question_types['text']
    
    def _clean_choices(self, choices: List[str]) -> List[str]:
        """Final cleanup of choice list"""
        cleaned = []
        seen = set()
        
        for choice in choices:
            choice = choice.strip()
            
            if len(choice) < 1:
                continue
            
            if choice.lower() in ['', 'none', 'n/a']:
                continue
            
            if choice not in seen:
                seen.add(choice)
                cleaned.append(choice)
        
        return cleaned


class LMSSession:
    """Manages LMS session per user"""
    
    def __init__(self, username: str, password: str, base_url: str = None):
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.base_url = base_url or "https://plsd.elearningcommons.com"
        self.login_url = f"{self.base_url}/login/index.php"
        self.extractor = QuestionExtractor()
        
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
        })
    
    def get_login_token(self) -> Optional[str]:
        """Retrieve login token from login page"""
        try:
            response = self.session.get(self.login_url, timeout=10)
            response.raise_for_status()
            
            patterns = [
                r'name="logintoken"\s+value="([^"]+)"',
                r'name="logintoken"\s*value="([^"]+)"',
                r'<input[^>]*name="logintoken"[^>]*value="([^"]+)"'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, response.text)
                if match:
                    return match.group(1)
            
            return None
            
        except RequestException as e:
            logger.error(f"Failed to get login token: {str(e)}")
            return None
    
    def login(self) -> bool:
        """Authenticate with the LMS"""
        logintoken = self.get_login_token()
        if not logintoken:
            return False
        
        login_data = {
            'anchor': '',
            'logintoken': logintoken,
            'username': self.username,
            'password': self.password
        }
        
        try:
            response = self.session.post(
                self.login_url, 
                data=login_data, 
                timeout=10, 
                allow_redirects=True
            )
            response.raise_for_status()
            
            if 'login' not in response.url.lower() or 'dashboard' in response.url.lower():
                return True
            
            return False
            
        except RequestException as e:
            logger.error(f"Login request failed: {str(e)}")
            return False
    
    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch a single page with error handling"""
        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            return response.text
        except Exception as e:
        
