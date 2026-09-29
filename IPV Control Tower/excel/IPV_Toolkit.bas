Attribute VB_Name = "IPV_Toolkit"
'==============================================================================
' IPV Toolkit - month-end helpers for the IPV pack (IPV_Pack_YYYYMM.xlsx)
'
' Install: open the pack, Alt+F11 > File > Import File... > IPV_Toolkit.bas,
'          then save the workbook as .xlsm (or import into PERSONAL.XLSB).
'
' Macros
'   FilterByDesk          - filter tblExceptions to one desk (prompted)
'   ClearFilters          - show every exception again
'   CheckSignOffs         - highlight CRITICAL/HIGH rows missing a valid review
'   SplitIntoDeskPacks    - one workbook per desk, saved next to the pack
'   DraftDeskEmails       - Outlook drafts (never sent) with each desk's items
'   ExportSignOffs        - CSV of reviewer decisions for `ipv signoffs-import`
'
' All macros address the Excel Table by name (tblExceptions) and its columns
' by header, so adding or reordering columns in the pack does not break them.
'==============================================================================
Option Explicit

Private Const EXC_SHEET As String = "Exceptions"
Private Const EXC_TABLE As String = "tblExceptions"
Private Const ENGINE_USER As String = "ipv-engine"

Private Function Exceptions() As ListObject
    Set Exceptions = ThisWorkbook.Worksheets(EXC_SHEET).ListObjects(EXC_TABLE)
End Function

Private Function Col(lo As ListObject, header As String) As Long
    ' 1-based column index within the table, or an error if the header is missing.
    Dim c As ListColumn
    For Each c In lo.ListColumns
        If LCase$(c.Name) = LCase$(header) Then Col = c.Index: Exit Function
    Next c
    Err.Raise vbObjectError + 513, "IPV_Toolkit", "Column '" & header & "' not found in " & lo.Name
End Function

Private Function Desks(lo As ListObject) As Collection
    ' Distinct desk names, in first-seen order.
    Dim seen As Object: Set seen = CreateObject("Scripting.Dictionary")
    Dim out As New Collection, r As ListRow, d As String
    For Each r In lo.ListRows
        d = CStr(r.Range.Cells(1, Col(lo, "desk")).Value)
        If Len(d) > 0 And Not seen.Exists(d) Then seen.Add d, True: out.Add d
    Next r
    Set Desks = out
End Function

'------------------------------------------------------------------------------
Public Sub FilterByDesk()
    Dim lo As ListObject: Set lo = Exceptions()
    Dim d As String
    d = InputBox("Desk to show (e.g. Credit, Rates, FX, Equities, Treasury ALM):", "IPV - filter")
    If Len(d) = 0 Then Exit Sub
    lo.Range.AutoFilter Field:=Col(lo, "desk"), Criteria1:=d
End Sub

Public Sub ClearFilters()
    Dim lo As ListObject: Set lo = Exceptions()
    If lo.AutoFilter.FilterMode Then lo.AutoFilter.ShowAllData
End Sub

'------------------------------------------------------------------------------
Public Sub CheckSignOffs()
    ' Four-eyes pre-check before the pack goes to the Head of Valuation Control:
    ' every CRITICAL/HIGH row needs a reviewer other than the engine, and a comment.
    Dim lo As ListObject: Set lo = Exceptions()
    Dim cSev As Long, cRev As Long, cCom As Long
    cSev = Col(lo, "severity"): cRev = Col(lo, "reviewer"): cCom = Col(lo, "commentary")
    Dim r As ListRow, sev As String, rev As String, missing As Long
    For Each r In lo.ListRows
        sev = CStr(r.Range.Cells(1, cSev).Value)
        rev = Trim$(CStr(r.Range.Cells(1, cRev).Value))
        r.Range.Cells(1, cRev).Interior.ColorIndex = xlNone
        r.Range.Cells(1, cCom).Interior.ColorIndex = xlNone
        If sev = "CRITICAL" Or sev = "HIGH" Then
            If Len(rev) = 0 Or LCase$(rev) = ENGINE_USER Then
                r.Range.Cells(1, cRev).Interior.Color = RGB(248, 208, 208): missing = missing + 1
            ElseIf Len(Trim$(CStr(r.Range.Cells(1, cCom).Value))) = 0 Then
                r.Range.Cells(1, cCom).Interior.Color = RGB(248, 208, 208): missing = missing + 1
            End If
        End If
    Next r
    If missing = 0 Then
        MsgBox "All CRITICAL and HIGH exceptions are reviewed and commented.", vbInformation, "IPV sign-off"
    Else
        MsgBox missing & " CRITICAL/HIGH exception(s) still need a reviewer or commentary (highlighted).", _
               vbExclamation, "IPV sign-off"
    End If
End Sub

'------------------------------------------------------------------------------
Public Sub SplitIntoDeskPacks()
    Dim lo As ListObject: Set lo = Exceptions()
    Dim d As Variant, wb As Workbook, path As String, n As Long
    Application.ScreenUpdating = False
    On Error GoTo Cleanup
    For Each d In Desks(lo)
        lo.Range.AutoFilter Field:=Col(lo, "desk"), Criteria1:=d
        Set wb = Workbooks.Add(xlWBATWorksheet)
        lo.Range.SpecialCells(xlCellTypeVisible).Copy
        wb.Worksheets(1).Range("A1").PasteSpecial xlPasteValuesAndNumberFormats
        wb.Worksheets(1).Name = Left$(Replace(CStr(d), "/", "-"), 31)
        wb.Worksheets(1).Columns.AutoFit
        path = ThisWorkbook.Path & Application.PathSeparator & "IPV_" & Replace(CStr(d), " ", "_") & ".xlsx"
        Application.DisplayAlerts = False
        wb.SaveAs Filename:=path, FileFormat:=xlOpenXMLWorkbook
        Application.DisplayAlerts = True
        wb.Close SaveChanges:=False
        n = n + 1
    Next d
Cleanup:
    Application.CutCopyMode = False
    Application.DisplayAlerts = True
    ClearFilters
    Application.ScreenUpdating = True
    If Err.Number <> 0 Then
        MsgBox "Stopped: " & Err.Description, vbCritical, "IPV desk packs"
    Else
        MsgBox n & " desk pack(s) saved to " & ThisWorkbook.Path, vbInformation, "IPV desk packs"
    End If
End Sub

'------------------------------------------------------------------------------
Public Sub DraftDeskEmails()
    ' Creates one Outlook draft per desk and displays it. Nothing is sent:
    ' the analyst reviews and sends each one.
    Dim lo As ListObject: Set lo = Exceptions()
    Dim ol As Object, mail As Object, d As Variant, r As ListRow, body As String, n As Long
    On Error Resume Next
    Set ol = GetObject(, "Outlook.Application")
    If ol Is Nothing Then Set ol = CreateObject("Outlook.Application")
    On Error GoTo 0
    If ol Is Nothing Then MsgBox "Outlook is not available.", vbExclamation: Exit Sub

    Dim cDesk As Long, cId As Long, cIns As Long, cSev As Long, cFlag As Long, cPv As Long
    cDesk = Col(lo, "desk"): cId = Col(lo, "exception_id"): cIns = Col(lo, "description")
    cSev = Col(lo, "severity"): cFlag = Col(lo, "rule_flags"): cPv = Col(lo, "pv_adjustment_usd")

    For Each d In Desks(lo)
        body = "<p>Hi " & d & " team,</p><p>Please provide evidence or commentary for the IPV exceptions " & _
               "below by the month-end sign-off deadline.</p><table border='1' cellpadding='4' " & _
               "style='border-collapse:collapse;font-family:Segoe UI;font-size:10pt'><tr><th>Exception</th>" & _
               "<th>Instrument</th><th>Severity</th><th>Rule hits</th><th>Indicated adjustment (USD)</th></tr>"
        n = 0
        For Each r In lo.ListRows
            If CStr(r.Range.Cells(1, cDesk).Value) = CStr(d) Then
                body = body & "<tr><td>" & r.Range.Cells(1, cId).Value & "</td><td>" & _
                       r.Range.Cells(1, cIns).Value & "</td><td>" & r.Range.Cells(1, cSev).Value & "</td><td>" & _
                       r.Range.Cells(1, cFlag).Value & "</td><td align='right'>" & _
                       Format$(r.Range.Cells(1, cPv).Value, "#,##0") & "</td></tr>"
                n = n + 1
            End If
        Next r
        If n > 0 Then
            Set mail = ol.CreateItem(0)
            mail.Subject = "IPV exceptions - " & d & " - " & n & " item(s)"
            mail.HTMLBody = body & "</table><p>Thanks,<br>Valuation Control</p>"
            mail.Display
        End If
    Next d
End Sub

'------------------------------------------------------------------------------
Public Sub ExportSignOffs()
    ' Writes exception_id, reviewer, status, commentary for rows a reviewer
    ' has completed. Load with:  python -m ipv signoffs-import <file>.csv
    Dim lo As ListObject: Set lo = Exceptions()
    Dim cId As Long, cRev As Long, cSt As Long, cCom As Long
    cId = Col(lo, "exception_id"): cRev = Col(lo, "reviewer")
    cSt = Col(lo, "workflow_status"): cCom = Col(lo, "commentary")
    Dim path As String, f As Integer, r As ListRow, n As Long, rev As String
    path = ThisWorkbook.Path & Application.PathSeparator & "signoffs_" & Format$(Now, "yyyymmdd_hhnn") & ".csv"
    f = FreeFile
    Open path For Output As #f
    Print #f, "exception_id,reviewer,status,commentary"
    For Each r In lo.ListRows
        rev = Trim$(CStr(r.Range.Cells(1, cRev).Value))
        If Len(rev) > 0 And Len(CStr(r.Range.Cells(1, cId).Value)) > 0 Then
            Print #f, Q(r.Range.Cells(1, cId).Value) & "," & Q(rev) & "," & _
                      Q(r.Range.Cells(1, cSt).Value) & "," & Q(r.Range.Cells(1, cCom).Value)
            n = n + 1
        End If
    Next r
    Close #f
    MsgBox n & " sign-off(s) exported to" & vbCrLf & path, vbInformation, "IPV sign-off export"
End Sub

Private Function Q(v As Variant) As String
    Q = """" & Replace(CStr(v), """", """""") & """"
End Function
