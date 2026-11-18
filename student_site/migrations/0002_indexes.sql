CREATE INDEX IF NOT EXISTS idx_students_group_name ON students(group_name);
CREATE INDEX IF NOT EXISTS idx_subjects_teacher_id ON subjects(teacher_id);
CREATE INDEX IF NOT EXISTS idx_grades_student_created ON grades(student_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_grades_subject_created ON grades(subject_id, created_at DESC, id DESC);
